from functools import reduce
import hmac
import logging
import math
import re
import uuid
from urllib.parse import urlencode

import botocore
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import Serializer, CharField, \
    IntegerField, BooleanField, PrimaryKeyRelatedField, ListField
from rest_framework.viewsets import ReadOnlyModelViewSet

from ..models import File, Membership, Resource, Symlink
from ..utils import mount, s3


EMAIL = re.compile('.+@.+\\..+')
User = get_user_model()


@mount('files')
class Files(ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    def _dereference(self, pk, user):
        '''we use this in all update routes to check for resource existence and
        relevant team permission
        get-only routes are optimized not to use this as they rely on symlinks
        to determine access level (all team members have READ access) and in
        many cases the resource will already be retrieved alongside the symlink
        '''
        return (Resource.objects.filter(pk=pk).first(),
                Resource.membership_for(pk, user))

    def _paginate(self, resources, cursor):
        '''we present a dumb os.read()-like pagination interface where the
        "next" cursor is always present and EOF is signalled by an empty read()
        (or more specifically, a read whose next cursor is equal to its
        original cursor)'''
        return {
            'entries': [resource.summary() for resource in resources],
            'next': str(resources[-1].id if len(resources) else cursor)
        }


    # GET /
    # a.k.a. GET RESOURCES SHARED WITH ME
    def list(self, request):
        '''
        returns a paginated list of the summaries of all the top level resources
        that are visible by the current user (e.g. symlinks)
        '''
        # no team-level permission check is needed for read-only access
        # symlink read access is however checked (e.g. to prevent WRITE_ANY
        # users without READ_ALL to see all folders)
        resources = Resource._meta.db_table
        symlinks = Symlink._meta.db_table
        cursor = request.query_params.get('cursor', 0)
        return Response(self._paginate(list(Resource.objects.raw(f'''
            select r.* from {symlinks} s, {resources} r
            where
                s.user_id = %s and (s.mask & %s) = %s and -- read access
                s.resource_id > %s and -- pagination
                s.resource_id = r.id -- join condition
            order by r.id asc
            limit %s
        ''', [request.user.id,
              Symlink.READ, Symlink.READ, # not all drivers support %(name)s
              cursor,
              settings.REST_FRAMEWORK['PAGE_SIZE']
        ])), cursor))


    # GET /pk
    # a.k.a. LIST FOLDER or DOWNLOAD FILE VARIANT
    def retrieve(self, request, pk=None):
        '''
        returns the metadata associated with this resource;

        for files, this contains the full description, including file metadata
        and presigned s3 access urls
        for folders, this is a paginated list of direct descendant summaries as
        well as all direct permissions

        for either, this also includes the name and all inherited permissions
        as well as the summary of the resource they are inherited from (if the
        user can read it, otherwise null)

        throws 403 if the resource cannot be viewed by the current user
        throws 404 if the resource does not exist

        note: this read-only view is not atomic for performance reasons; as such
        its output should only be considered eventually consistent
        '''
        resource = None  # cache resource if a direct symlink is available
        has_perm = False
        for symlink in Resource.symlinks_for(pk, request.user):
            if symlink.resource_id == pk:
                resource = symlink.resource
            if symlink.has_permission(Symlink.READ):
                has_perm = True  # can't use for+break+else here because the
                                 # client needs to differentiate 404s from 403s
                break

        try:
            resource = resource or Resource.objects.get(pk=pk)  # cache miss
        except Resource.DoesNotExist:
            return Response({'detail': 'Resource not found'}, 404)
        if not has_perm:
            return Response({'detail': 'You may not view this resource'}, 403)

        # folder response is a partial summary with a paginated list of contents
        if resource.kind == 'folder':
            cursor = request.query_params.get('cursor', 0)
            return Response(
                dict(
                    resource.summary(),
                    children=self._paginate(
                        list(Resource.objects.filter(
                            folder=resource,  # only direct descendants
                            id__gt=cursor     # from the current page
                        ).order_by('id')[:settings.REST_FRAMEWORK['PAGE_SIZE']]),
                        cursor
                    )
                )
            )
        # file response is a full summary including presigned s3 get urls
        return Response(resource.summary(full=True))


    # PUT /pk/members
    # a.k.a. SET SHARED WITH
    #@transaction.atomic  # this is always atomic when called by members()
    def _update_members(self, request, pk):
        '''
        this is called by members() inside a transaction, when the method is PUT
        it will return a response on error and None on success, allowing the GET
        code in members to return the updated permissions
        '''
        # team permission check
        resource, member = self._dereference(pk, request.user)
        if not resource:
            return Response({'detail': 'Resource not found'}, 404)
        if not (member and member.has_permission(Membership.WRITE)):
            return Response({'detail': 'You may not share resources belonging '
                                       'to this team'}, 403)
        team = member.team

        # permission check: for folders, SHARE and OWNER must be inherited
        # for files, SHARE and OWNER may be direct
        max_mask = 0  # grantable to new members
        can_share = False
        is_owner = False
        for symlink in Resource.symlinks_for(resource, request.user):
            max_mask |= symlink.mask
            if symlink.resource == resource and resource.kind == 'folder':
                continue  # the current resource is a folder, share and owner don't apply
            if symlink.has_permission(Symlink.SHARE):
                can_share = True
            if symlink.has_permission(Symlink.OWNER):
                is_owner = True

        # since this method may have side effects (send invitation emails),
        # we want to collect all the updates and apply them atomically after
        # we're certain that the operation will succeed
        to_delete = set()
        to_save = set()

        # update existing resource's members' masks
        for symlink in Symlink.objects.filter(resource=resource):
            if str(symlink.user.id) in request.data:
                if not is_owner:
                    return Response({'detail': 'You may not manage this '
                                               'resource\'s members'}, 403)

                try:
                    mask = int(request.data.pop(symlink.user.id))
                except ValueError:
                    return Response({'detail': 'Invalid resource members'}, 400)
                if mask == 0:
                    to_delete.add(symlink)
                elif mask != symlink.mask:
                    symlink.mask = request.data[symlink.user.id]
                    to_save.add(symlink)

        # existing members have been popped from request.data, anything left
        # are users that don't have any direct access to this resource
        if len(request.data):
            if not can_share:
                return Response({'detail': 'You may not share this resource'}, 403)

            # index referenced users by id or email (both are valid keys for the api)
            existing_users = {}
            for user in (
                User.objects.filter(pk__in=request.data.keys()) |
                User.objects.filter(email__in=request.data.keys())
            ):
                existing_users[user.id] = existing_users[user.email] = user

            for key, mask in request.data.items():
                try:
                    if (mask := int(mask)) == 0:  # can throw if not a number
                        raise ValueError  # new users must have some access
                except ValueError:
                    return Response({'detail': f'Invalid mask for member {key}'}, 400)
                if (mask & max_mask) != mask:
                    return Response({'detail': f'You may not grant mask {mask}'}, 403)

                if key in existing_users:
                    # there's an existing user in our db with either the same
                    # email address as key, or the same user id as key
                    user = existing_users[key]
                elif isinstance(key, str) and EMAIL.match(key):
                    # user doesn't exist, but key looks like an email address
                    # generate a new user but don't save it and check for the
                    # appropriate invite permission later
                    password = User.generate_password()
                    user = User.objects.create_user(key, password)
                    to_save.add(user)
                    # TODO: enqueue account creation email
                else:
                    # key is neither the id of an existing user nor a valid email address
                    return Response({'detail': f'Invalid user id: {key}'}, 400)

                # check if the new user is already a member of this team and if
                # not, make them one (assuming the caller has permission to)
                # TODO: these n queries can be reduced to a single join on the
                # existing_users query above, however that result can't be
                # mapped on the user model because `teams` is many-to-many
                if not (
                    new_member := user.teams.filter(team=team).first()
                    and new_member.has_permission(Membership.READ)
                ):
                    if not member.has_permission(Membership.INVITE):
                        return Response({'detail': 'You may not invite users '
                                                   'to join this team'}, 403)
                    team.used_members += 1
                    if team.used_members > team.member_quota:
                        return Response({'detail': 'Team member quota exceeded'}, 422)

                    to_save.add(team)

                    # no explicit team mask possible in this api call; allow
                    # invited user to do the same basic stuff as the inviter
                    to_save.add(Membership(user=user,
                                           team=team,
                                           mask=member.mask & 7))
                    # TODO: enqueue team invitation email

                # at this point, user is a team member
                to_save.append(Symlink(user=user,
                                       resource=resource,
                                       mask=mask))
                # TODO: enqueue sharing notification email

        # commit transaction after all sanity checks have been performed
        # TODO: send enqueued emails
        for model in to_save:
            model.save()
        for model in to_delete:
            model.delete()
        return None


    # GET /pk/members
    # a.k.a. GET SHARED WITH
    @action(detail=True, methods=['get', 'put'])
    @transaction.atomic
    def members(self, request, pk=None):
        '''
        retrieves the full hierarchical permission set of this resource (GET)
        or updates sharing settings on THIS resource but NOT any parent folder
        permissions (PUT)

        returns a dictionary mapping user ids to an extended user structure that
        contains their permission mask as well as the full hierarchy of objects
        that granted them the mask

        for update, it accepts a dictionary mapping user ids (or email
        addresses) to their new mask; any existing user that's missing from the
        dictionary keeps their old permissions and any user mapped to 0 is
        removed from the resource entirely
        a new user can only be granted a subset of the calling user's mask

        requires SHARE to add team members or OWNER on a parent folder to alter
        or remove an existing member's permissions for this resource
        also requires INVITE if the target user is not already part of the team
        that owns this resource

        throws 403 if the user does not have permission to perform the operation
        throws 404 if resource does not exist (update only, read returns 403)
        '''
        if request.method == 'PUT' and (err := self._update_members(request, pk)):
            return err

        # return current permission status, including any changes made by PUT
        shared_with = {}
        has_perm = False
        is_owner = False
        for symlink in reversed(Resource.symlinks_for(pk)):
            if symlink.user == request.user and symlink.has_permission(Symlink.READ):
                has_perm = True  # user can see this resource and all subresources

            if symlink.resource.folder and symlink.has_permission(Symlink.READ):
                # hide users with escalated permissions and users that can read
                # any team resource
                shared_with.setdefault(symlink.user, []).append({
                    'mask': symlink.mask,
                    'on': symlink.resource.summary() if has_perm
                          else None,       # i.e. "some unknown parent folder"
                    'removable': is_owner  # frontend helper - can be computed locally
                                           # but it's almost free to do so here;
                                           # frontend still needs to check that all
                                           # inheritance sources are removable for
                                           # a particular permission
                })

            if symlink.user == request.user and symlink.has_permission(Symlink.OWNER):
                is_owner = True  # current user can change descendant permissions

        if not has_perm:
            return Response({'detail': 'You may not view this resource'}, 403)
        return Response({
            user.id: dict(user.summary(), permissions={
                'mask': reduce(lambda acc, perm: acc | perm['mask'], perms, 0),
                'hierarchy': perms
            })
            for user, perms in shared_with.items()
        })


    def _sign_upload(self, *, user, bucket, key, upload_id, folder, name, size):
        '''signs a multipart commit request's parameters with the current
        user's auth token'''
        user = str(getattr(user, 'id', user)).encode('utf-8')
        bucket = str(bucket).encode('utf-8')
        key = str(key).encode('utf-8')
        upload_id = str(upload_id).encode('utf-8')
        folder = str(getattr(folder, 'id', folder)).encode('utf-8')
        name = str(name).encode('utf-8')
        size = str(int(size)).encode('utf-8')
        return hmac.new(settings.SECRET_KEY.encode('utf-8'),
                        b'.'.join([user, upload_id, folder, name, size]),
                        'sha256').hexdigest()


    # POST /pk
    # a.k.a. COMMIT AWS FILE UPLOAD TO FOLDER
    @action(detail=True, methods=['post'])
    @transaction.atomic
    def commit(self, request, pk=None):
        '''
        commits a pending multipart upload to s3 and creates the resource
        object attached to it; this request is signed with the user's id and
        the server's secret key

        it expects a list of etags as received by the most recent successful
        upload response for each part (as `parts`)

        throws 401 if the request signature does not match: this should only
        happen if you attempt to commit when signed in as a different user from
        the one that called update in the first place, or in the rare instance
        that we rotate our server key

        throws 400 if aws rejected the etags that were sent, or if the
        multipart upload request expired and the file parts were deleted

        throws 403 if the user's write privileges have been revoked since
        starting the multipart upload session

        throws 409 if in between the time the upload was started and now a new
        resource was created with the same name; the client may resolve this by
        resubmitting with an extra `name` and/or `folder` parameter

        for security reasons, the conflict resolution parameters are ignored
        when there is no conflict (e.g. if the conflict gets resolved between
        the time of the original 409 commit and the subsequent altered commit
        with the new name and/or folder, the original name / folder will be
        used) - client can determine the resource's final location by
        inspecting the response which is always the summary representation of
        the new resource

        throws 422 and aborts the multipart request if a team quota (resource
        or storage) is exceeded by 10% (this error margin allows the upload to
        go through if some other concurrent operation was completed before this
        one however the limit is still enforced to limit abuse)

        example request:

        POST /commit?some_opaque_string
        {
            parts: ['asd', 'fgh', 'jkl'],
            name: 'my new file (2)'  # only used on name conflict
            folder: 1234  # only used when the previous folder was deleted;
                          # any folder may be used as long as the user has
                          # write access to it (it will be billed to whatever
                          # team ends up owning it)
        }
        '''
        # validate signature (DoS protection)
        signature = request.query_params.get('signature', '')
        bucket = request.query_params.get('bucket', '')
        key = request.query_params.get('key', '')
        upload_id = request.query_params.get('upload_id', '')
        folder = request.query_params.get('folder', '')
        name = request.query_params.get('name', '')
        size = request.query_params.get('size', '')
        if not hmac.compare_digest(signature, self._sign_upload(
            user=request.user,
            # aws
            bucket=bucket, key=key, upload_id=upload_id,
            # local
            folder=folder, name=name, size=size
        )):
            return Response({'detail': 'Invalid request signature'}, 401)

        # check team permissions and parent folder
        target, member = self._dereference(pk, request.user)
        if not target:
            # try a different folder on folder conflict
            if name and (folder := request.data.get('folder')):
                target, member = self._dereference(folder, request.user)
            if not target:
                return Response({'detail': 'Resource was deleted'}, 409)

        if not (member and member.has_permission(Membership.WRITE)):
            return Response({'detail': 'You may not modify resources '
                                       'that belong to this team'}, 403)
        team = member.team

        # check that a file with the same name does not already exist in the
        # target folder; overwriting an existing file is allowed
        if name and Resource.objects.filter(folder=target, name=name).first():
            # try a different name on file conflict
            if not (name := request.data.get('name')) \
            or Resource.objects.filter(folder=target, name=name).first():
                return Response({'detail': 'A resource with the same name '
                                           'already exists'}, 409)

        # check permissions on target
        for symlink in Resource.symlinks_for(target, request.user):
            if symlink.has_permission(Symlink.WRITE):
                break
        else:
            return Response({'detail': 'You may not modify this resource'}, 403)

        # check quotas; this was deferred during update() because the file was
        # not yet uploaded; however we need to check this now and abort if the
        # quotas are exceeded by a (controllable) percent
        team.used_resources += 1
        team.used_storage += int(size)  # was a str for signature validation
        quota_error = None
        if team.used_resources > team.resource_quota * settings.UPLOAD_QUOTA_OVERCOMMIT:
            quota_error = 'Team resource quota exceeded'
        if team.used_storage > team.storage_quota * settings.UPLOAD_QUOTA_OVERCOMMIT:
            quota_error = 'Team storage quota exceeded'
        if quota_error:
            try:
                s3.abort_multipart_upload(**{
                    'Bucket': bucket,
                    'Key': key,
                    'UploadId': upload_id
                })
            except Exception:
                '''best-effort only; aws will eventually delete the uploaded
                but uncommitted parts regardless:
                https://aws.amazon.com/blogs/aws/s3-lifecycle-management-update-support-for-multipart-uploads-and-delete-markers/
                '''
            return Response({'detail': quota_error}, 422)

        if not (parts := request.data.get('parts')):
            return Response({'detail': '`parts` is required'}, 400)
        try:
            s3.complete_multipart_upload(**{
                'Bucket': bucket,
                'Key': key,
                'MultipartUpload': {
                    'Parts': [{
                        'ETag': str(etag),
                        'PartNumber': num + 1
                    } for num, etag in enumerate(parts)]
                },
                'UploadId': upload_id
            })
        except TypeError:
            return Response({'detail': '`parts` must be an array'}, 400)
        except botocore.exceptions.ClientError as err:
            try:
                code = err.response['Error']['Code']
            except Exception:
                code = ''
            if code == 'NoSuchUpload':
                return Response({'detail': 'Session expired. Please retry the upload'}, 400)
            if code == 'InvalidPart':
                return Response({'detail': 'Invalid ETag value found in `parts`'}, 400)
            logging.exception('Unhandled exception while committing upload')
            return Response({'detail': 'Internal server error'}, 500)
        else:
            file = File.objects.create(team=team,
                                       bucket=bucket,
                                       key=key,
                                       size=size)
            to_update = dict(modified_by=request.user,
                             kind='file',  # TODO: enqueue async mime update
                             original={'id': file.id},
                             variants=[])
            if name:
                # new file in folder
                resource = Resource.objects.create(folder=target,
                                                   name=name,
                                                   created_by=request.user,
                                                   **to_update)
            else:
                # replace existing file's contents
                target.files.clear()
                for key, value in to_update.items():
                    setattr(target, key, value)
                target.save()

            file.references.add(resource)
            team.save()  # update usage quotas
            return Response(resource.summary(True), 201)

    # PUT /pk
    # a.k.a. CREATE FOLDER OR UPLOAD FILE OR COPY / MOVE RESOURCE
    @transaction.atomic
    def update(self, request, pk=None):
        '''
        when called on a FILE, it will start a replace operation
        * provide 'size' to get a list of presigned s3 urls and a final (also
          presigned) local commit url
        * provide 'from' to copy an existing FILE to this location

        when called on a FOLDER, it will create a new resource either by direct
        upload or by copying or moving an existing resource
        * provide 'name' to create a new subfolder
        * provide 'name' and 'size' to upload a new file (presigned urls)
        * provide 'name' and 'from' to create a copy of the 'from' resource in
          this folder

        any other combination results in an error

        whenever 'from' is used in combination with `delete`, the original
        resource will be atomically deleted, turning this 'copy' into a 'move';
        for files, this is optimized internally by copying the s3 references to
        the new file and discarding the old ones, no actual data copying is done

        a move updates the name and folder of the source node, keeping
        shared_with from the source

        a copy replaces the contents of the destination node, keeping
        shared_with from the target

        permissions:
        * requires WRITE on resource
        * when copying, requires READ on the source
        * when copying or moving, requires SHARE on the source unless both
          source and destination inherit WRITE from the same folder
          (copying the resource within the same subtree is not considered sharing)
        * when moving, requires WRITE on the source's parent folder

        throws 404 if the resource does not exist
        throws 409 if a resource with the same name already exists in the folder
        throws 422 if this operation would exceed the team's resource quota
        '''
        # team permission check
        target, member = self._dereference(pk, request.user)
        if not target:
            return Response({'detail': 'Resource not found'}, 404)
        if not (member and member.has_permission(Membership.WRITE)):
            # READ permission is implied to all members
            return Response({'detail': 'You may not modify resources that '
                                       'belong to this team'}, 403)
        team = member.team

        # target permissions check
        writable_ancestors = set()
        can_write = False
        for symlink in Resource.symlinks_for(pk, request.user):
            if symlink.has_permission(Symlink.WRITE):
                can_write = True
                writable_ancestors.add(symlink.resource)
        if not can_write:
            return Response({'detail': 'You may not modify this resource'}, 403)

        # parse args
        name = str(request.data.get('name', ''))
        try:
            size = request.data.get('size')
            if size is not None:
                size = int(size)
        except ValueError:
            return Response({'detail': '`size` must be numeric'}, 400)
        try:
            _from = request.data.get('from')
        except ValueError:
            return Response({'detail': 'Invalid referenced resource'}, 400)
        delete = bool(request.data.get('delete', False))

        # arg sanity check
        if bool(name) != (target.kind == 'folder'):
            return Response({'detail': '`name` is required for folders and '
                                       'invalid for files'}, 400)
        if _from is not None == size is not None:
            return Response({'detail': 'you must specify exactly one of '
                                       '`from` or `size`'}, 400)
        if _from is None and delete:
            return Response({'detail': '`delete` can only be used with `from`'}, 400)

        # overwrite check
        if name and Resource.objects.filter(folder=target, name=name).first():
            return Response({'detail': 'A resource with the same name already exists'}, 409)

        # NEW FOLDER OR FILE UPLOAD
        if not _from:
            # resource quota check
            team.used_resources += 1
            if team.used_resources > team.resource_quota:
                return Response({'detail': 'Team resource quota exceeded'}, 422)

            if not size:
                # NEW FOLDER; update usage and create now
                team.save()
                return Response(Resource.objects.create(folder=target,
                                                        name=name,
                                                        created_by=request.user,
                                                        modified_by=request.user,
                                                        kind='folder').summary(), 201)

            # FILE UPLOAD
            # check storage quota but don't update usage because it may be aborted
            team.used_storage += size
            if team.used_storage > team.storage_quota:
                return Response({'detail': 'Team storage quota exceeded'}, 422)

            # generate key and start a multipart s3 upload
            key = str(uuid.uuid4())
            bucket = settings.AWS['S3_BUCKET']
            upload_id = s3.create_multipart_upload(**{
                'Bucket': bucket,
                'Key': key,
                # meta
                'ContentDisposition': 'inline',
                'ACL' :'private'
            })['UploadId']

            # s3 accepts at most 10.000 parts of at least 5MiB each (except the remainder)
            part_size = max(6*(1<<20), math.ceil(size/10000))
            parts = []
            part = offset = 0
            while offset < size:
                # the signatures for the urls are computed on the backend
                # without calling into aws at all; however because the
                # underlying credentials used to sign the request are subject
                # to rotation, signed urls with a longer expiration period are
                # not guaranteed to work; see also:
                # https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html
                parts.append({
                    'url': s3.generate_presigned_url(**{
                        'ClientMethod': 'upload_part',
                        'Params': {
                            'Bucket': bucket,
                            'Key': key,
                            'PartNumber': (part := part + 1),
                            'UploadId': upload_id
                        },
                        'ExpiresIn': 24 * 3600
                    }),
                    'start': offset,
                    'end': (offset := min(offset + part_size, size))
                })

            # sign our own commit url; this will not only commit the url on aws
            # but also create the actual resource and update quota
            args = dict(user=request.user.id,
                        bucket=bucket,
                        key=key,
                        upload_id=upload_id,
                        folder=target.id,
                        name=name,
                        size=size)
            commit = self.reverse_action(self.commit.url_name, pk)
            commit += '&' if '?' in commit else '?'
            commit += urlencode(dict(args, signature=self._sign_upload(**args)))

            # user will have to talk to s3 prior to completing this process
            return Response(dict(parts=parts, commit=commit), 202)

        # COPY OR MOVE OPERATION
        # make sure that the user has read (and maybe share) privileges on the source
        _from = int(_from)
        source = None
        can_copy = False
        can_read = False
        can_delete = False
        for symlink in Resource.symlinks_for(_from, request.user):
            if symlink.resource_id == _from:
                source = symlink.resource  # cache source
            elif symlink.has_permission(Symlink.WRITE):  # folder write
                can_delete = True
            if symlink.has_permission(Symlink.READ):
                can_read = True
            elif symlink.has_permission(Symlink.SHARE) \
            and member.has_permission(Membership.SHARE) \
            or symlink.resource in writable_ancestors:
                can_copy = True

        if not can_read:
            return Response({'detail': 'You may not view the referenced resource'}, 403)
        if not can_copy:
            return Response({'detail': 'You may not copy the referenced resource'}, 403)

        if delete:
            if not can_delete:
                return Response({'detail': 'You may not move the referenced resource'}, 403)

            # moving is simple: update source parent folder and name; no need
            # to check quotas because they are the same after the operation
            # also, according to spec, moving involves transferring the
            # existing permissions so no need to do anything else there
            try:
                source = source or Resource.objects.get(pk=pk)  # cache miss
            except Resource.DoesNotExist:
                return Response({'detail': 'Referenced resource not found'}, 404)

            source.folder = target
            source.name = name
            source.save()
            return Response(source.summary())

        # copying is more involved as all subresources must be cloned
        resources = Resource._meta.db_table
        __depth = '__depth'  # ensure resultset is ordered breadth first
        all_resources = list(Resource.objects.raw(f'''
            with recursive p as (
                select r.*, 0 {__depth} from {resources} r
                    where r.id = %s
                union select r.*, {__depth} + 1 from {resources} r, p
                    where r.folder_id = p.id
            )
                select p.* from p order by {__depth} asc
            ''', [source.id]
        ))

        # team quota check; since we implement copy on write, there's no
        # need to check the storage quota here as it remains unchanged
        team.used_resources += len(all_resources)
        if team.used_resources > team.resource_quota:
            return Response({'detail': 'Team resource quota exceeded'}, 422)
        team.save()

        # we do a breadth-first search to select all underlying resources and
        # keep track of the copied resource ids for their descendants' folders
        # for files, while we don't have to copy the permissions, we do have to
        # create new references to all s3 resources to prevent them from
        # getting garbage collected
        mapping_table = {source.folder: target}
        result = None
        for resource in all_resources:
            copy = Resource.create(folder=mapping_table[resource.folder],
                                   name=name if resource == target else resource.name,
                                   created_by=request.user,
                                   modified_by=request.user,
                                   kind=resource.kind,
                                   original=resource.original,
                                   variants=resource.variants)
            copy.files.add(*resource.files.all())
            mapping_table[resource] = copy
            result = result or copy
        return Response(result.summary(), 201)


    # DELETE /pk
    # a.k.a. DELETE FILE OR FOLDER
    @transaction.atomic
    def destroy(self, request, pk=None):
        '''
        deletes this resource and (for folders) any subfolders and their files

        requires WRITE on PARENT folder
        throws 404 if resource does not exist
        '''
        resource, member = self._dereference(pk, request.user)
        if not resource:
            return Response({'detail': 'Resource not found'}, 404)
        if not (member and member.has_permission(Membership.WRITE)):
            return Response({'detail': 'You may not delete resources belonging '
                                       'to this team'}, 403)

        # permissions check
        for symlink in Resource.symlinks_for(pk, request.user):
            if symlink.resource != resource \
            and symlink.has_permission(Symlink.WRITE):  # write on parent folder
                break
        else:
            return Response({'detail': 'You may not delete this resource'}, 403)

        # delete resource; child resources will cascade; s3 assets will be
        # deleted asyncronously
        resource.delete()
        return Response({'detail': 'Resource deleted'}, 202)


    # this code is only used to render the django rest_framework ui; this is
    # very broken atm, so don't expect much
    queryset = Resource.objects.all()

    def get_serializer_class(self):
        if self.action == 'members':
            class serializer_class(Serializer):
                '''this is an arbitrary dict()'''
        elif self.action == 'commit':
            class serializer_class(Serializer):
                name = CharField(required=False,
                                 allow_null=True)
                folder = PrimaryKeyRelatedField(queryset=Resource.objects.filter(kind='folder')
                                                                         .all(),
                                                required=False,
                                                allow_null=True)
                parts = ListField(CharField(required=True),
                                  allow_empty=False,
                                  max_length=10000)
        else:
            class serializer_class(Serializer):
                name = CharField(required=False)
                size = IntegerField(required=False)
                vars()['from'] = PrimaryKeyRelatedField(queryset=Resource.objects.all(),
                                                        required=False,
                                                        allow_null=True)
                delete = BooleanField(required=False)
        return serializer_class
