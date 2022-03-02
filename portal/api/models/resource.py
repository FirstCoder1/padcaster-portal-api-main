from django.contrib.auth import get_user_model
from django.db import connection, models

from .team import Team, Membership
from ..utils import s3, RawQueryBuilder


User = get_user_model()


class Symlink(models.Model):
    class Meta:
        db_table = 'resource_user_link'
        constraints = [
            models.UniqueConstraint(fields=['user', 'resource'],
                                    name='file_sharing')
        ]

    resource = models.ForeignKey('Resource', on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    # the max value supported by postgres smallint field is (1<<15)-1 which is
    # what all of the below permissions amount to if combined via |
    # promote this to integer or bigint if you require more
    mask = models.SmallIntegerField()  # when this contains DELETE, user prompt is required
                                       # to determine whether it's the resource or the symlink
                                       # that is being deleted

    # permissions
    READ = 1                   # can view this resource and any resources it may contain
                               # unsetting this usually deletes the symlink, though it is
                               # possible to have WRITE_ONLY or SHARE_ONLY permissions on
                               # some resources (e.g. team root for WRITE_ANY / SHARE_ANY)
    WRITE_ONLY = 2             # relevant for privilege escalation, currently implemented
                               # on the team root folder via membership pseudo-perms
    WRITE = WRITE_ONLY | READ  # can replace this resource and any resources it may contain
    SHARE_ONLY = 4             # relevant for privilege escalation, currently implemented
                               # on the team root folder via membership pseudo-perms
    SHARE = SHARE_ONLY | READ  # can share this resource and any resources it may contain
                               # with any team member, only having a subset of the caller's
                               # permissions, defaulting to WRITE + SHARE (e.g. fork())
    OWNER = 15                 # can grant any permission to any team member, and can
                               # revoke previously granted permissions; this is usually
                               # inherited from the user's root folder which must at all
                               # times have at least one owner

    #PERM = 16
    #PERM = 32
    #PERM = 64
    #PERM = 128
    #PERM = 256
    #PERM = 512
    #PERM = 1024
    #PERM = 2048
    #PERM = 4096
    #PERM = 8192
    #PERM = 16384
    def has_permission(self, value):
        return (self.mask & value) == value


class File(models.Model):
    '''A file that exists on s3; it is referenced by one or more `Resource`s
    and eventually deleted once its ref count drops to 0'''
    class Meta:
        db_table = 'file'

    team = models.ForeignKey(Team,
                             on_delete=models.RESTRICT,
                             related_name='+')  # quota target
    bucket = models.CharField(max_length=100)  # s3 bucket name
    key = models.CharField(max_length=200)  # s3 key in bucket
    size = models.IntegerField()  # size in bytes (for quota management)

    references = models.ManyToManyField('Resource',  # s3 asset can be deleted
                                        related_name='files',  # when this is 0
                                        db_table='resource_file_link')

    @property
    def url(self):
        # the signature is computed on the backend without calling into aws at
        # all; however because the underlying credentials used to sign the
        # request are subject to rotation, signed urls with a longer expiration
        # period are not guaranteed to work; see also:
        # https://docs.aws.amazon.com/AmazonS3/latest/userguide/ShareObjectPreSignedURL.html
        return s3.generate_presigned_url(**{
            'ClientMethod': 'get_object',
            'Params': {
                'Bucket': self.bucket,
                'Key': self.key
            },
            'ExpiresIn': 24 * 3600
        })


class Resource(models.Model):
    '''Resources belong to a team and do not host permissions themselves,
    instead permissions are granted to team members via symlinks, which is the
    through table of the members field; however permissions are inherited and
    django's ORM does not currently support recursive queries so while they are
    defined in the model for the sake of schema documentation, they are not
    directly used.'''
    class Meta:
        db_table = 'resource'
        constraints = [
            models.UniqueConstraint(fields=['folder', 'name'],
                                    name='filesystem_invariant')
        ]

    # non-null everywhere except Team root folders, folder.kind must be 'folder'
    # standard hierarchy is Team/User/resource...
    # where Team is symlinked to team members that have READ_ALL
    # and User is symlinked to each individual team member
    # on user delete, either the folder is updated to point to the destination
    # user's folder or deleted altogether; the symlink will be cascade
    folder = models.ForeignKey('self',
                               null=True,
                               on_delete=models.CASCADE,
                               related_name='children')  # filesystem tree
    name = models.CharField(max_length=200)  # unique filename per non-null node

    created = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User,
                                   null=True,  # null means "deleted member"
                                   on_delete=models.SET_NULL,
                                   related_name='+')

    modified = models.DateTimeField(auto_now=True)
    modified_by = models.ForeignKey(User,
                                    null=True,  # null means "deleted member"
                                    on_delete=models.SET_NULL,
                                    related_name='+')

    KINDS = {
        'folder': 'Folder',    # not processed - can contain children
        'file': 'File',        # arbitrary blob, not processed
        'picture': 'Picture',  # processed with vips on the backend
        'video': 'Video'       # processed with ffmpeg on the sqs queue
    }
    kind = models.CharField(max_length=10, choices=KINDS.items())
    '''
    a 'meta' struct, as used by 'original' and 'variants' depends on 'kind':
    Folder = null                # folders do not contain any metadata
    File {                       # used when kind == 'file' (e.g. unknown)
        'id': int                # the id of the associated `File` object;
                                 # must be present in `files`
    }
    Picture: File {              # used when kind == 'picture'
        'width': int             # presentation width in pixels
        'height': int            # presentation height in pixels
        'offset': float          # frame offset in seconds; only for thumbnails
    }
    Video: File {                # used when kind == 'video'
        'duration': float        # total duration in seconds
        'video': {
            'codec': str         # video codec used, e.g. h264
            'width': int         # presentation width in pixels at DAR 1:1
            'height': int        # presentation height in pixels at DAR 1:1
            'framerate': float   # average framerate
        },
        'audio': {
            'codec': str         # audio codec used, e.g. aac
            'channels': int      # number of discrete channels, e.g. 1, 2 or 6
            'frequency': int     # sampling rate, in Hz
        },
        'thumbnails': [Picture]  # list of thumbnails extracted from video
        'log': {                 # path to encoding log; only present in
            bucket: str          # > variants and thumbnails; stripped;
            key: str             # > includes billing id, cmdline, total rusage
        }
    }
    '''
    original = models.JSONField(null=True)  # meta; set to null on delete
    variants = models.JSONField(null=True)  # array of meta; set to null on delete

    def summary(self, full=False):
        '''Returns this resource as a json representable structure'''
        result = {
            'id': self.id,
            'name': self.name,
            'kind': self.kind,
            'folder': self.folder_id,
            'created': {
                'on': self.created.isoformat(),
                'by': self.created_by.summary() if self.created_by else None
            },
            'modified': {
                'on': self.modified.isoformat(),
                'by': self.modified_by.summary() if self.modified_by else None
            }
        }
        if self.kind != 'folder' and full:
            files = {file.id: file for file in self.files.all()}

            def summarize(file):
                '''Redacts sensitive information from file and adds s3 signed
                access urls

                this method mutates and returns `file` so the parent resource
                can no longer be safely updated via django's orm; intended use
                case is right before returning the results to the client
                '''
                file.pop('log', None)  # hide ffmpeg logs
                _id = file.pop('id')
                file['url'] = files[_id].url
                for thumb in file.get('thumbnails', []):
                    summarize(thumb)
                return file

            result['original'] = None if not self.original else summarize(self.original)
            result['variants'] = [] if not self.variants else \
                                 [summarize(variant) for variant in self.variants]
        return result

    @classmethod
    def symlinks_for(cls, resource, user=None, db='default'):
        '''for a user to have a permission on a resource, there must be at least
        one symlink between the user and the resource or any of its parent folders

        this method returns all symlinks on that path (if any), starting from
        the file and going up the tree up to the root with the resource and
        user fields pre-populated (i.e. like Symlink.select_related())

        if user is None, it will return all symlinks for all users, else only
        the symlinks for that particular user (slightly faster usually)
        '''
        # table names
        resources = Resource._meta.db_table
        symlinks = Symlink._meta.db_table
        users = User._meta.db_table

        # filters
        resource = getattr(resource, 'id', resource)
        if user and not isinstance(user, User):
            user = User.objects.get(user)  # must be a model to populate the cache

        with connection.cursor() as cursor:
            builder = RawQueryBuilder(Symlink, 's')
            builder.select_related('resource_id', 'p')

            __height = '__height'  # virtual column name, used for sorting
            base_query = f'''
                with recursive p as (
                    select r.*, 0 {__height} from {resources} r
                        where r.id = %s
                    union select r.*, {__height} + 1
                        from {resources} r, p
                        where r.id = p.folder_id
                )'''
            if user:
                # user is always the same
                builder.select_related('user_id', user)
                cursor.execute(f'''{base_query}
                    select {builder.cols} from {symlinks} s, p
                    where s.user_id = %s and s.resource_id = p.id
                    order by {__height} asc
                    ''', [resource, user.id])
            else:
                # list all users in insertion order; this method is expected to
                # be fast so join them here to avoid expensive follow up queries
                builder.select_related('user_id', 'u')
                cursor.execute(f'''{base_query}
                    select {builder.cols} from {symlinks} s, p, {users} u
                    where s.user_id = u.id and s.resource_id = p.id
                    order by {__height} asc, s.id asc
                    ''', [resource])

            return [builder.row_to_model(row) for row in cursor]

    @classmethod
    def membership_for(cls, resource, user, db='default'):
        '''returns user's membership to resource's team as a prepopulated
        membership object'''
        resources = Resource._meta.db_table
        teams = Team._meta.db_table
        membership = Membership._meta.db_table
        users = User._meta.db_table

        resource = getattr(resource, 'id', resource)
        user = getattr(user, 'id', user)

        with connection.cursor() as cursor:
            builder = RawQueryBuilder(Membership, 'm')
            builder.select_related('user_id', 'u')
            builder.select_related('team_id', 't')

            cursor.execute(f'''
                with recursive p as (
                    select r.* from {resources} r
                        where r.id = %s
                    union select r.*
                        from {resources} r, p
                        where r.id = p.folder_id
                )
                    select {builder.cols} from {membership} m, {teams} t, {users} u, p
                    where
                        p.folder_id is null and
                        t.root_id = p.id and
                        u.id = %s and
                        m.team_id = t.id and
                        m.user_id = u.id
            ''', [resource, user])
            row = cursor.fetchone()
            return row if row is None else builder.row_to_model(row)
