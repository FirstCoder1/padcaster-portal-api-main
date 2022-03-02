from django.contrib.auth import get_user_model
from django.db import models


User = get_user_model()


class Membership(models.Model):
    class Meta:
        db_table = 'team_user_link'
        constraints = [
            models.UniqueConstraint(fields=['user', 'team'],
                                    name='team_sharing')
        ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    team = models.ForeignKey('Team', on_delete=models.CASCADE)

    # the max value supported by postgres integer field is (1<<31)-1 which is
    # what all of the below permissions amount to if combined via |
    # an additional 32 values can be obtained by promoting the mask to bigint
    mask = models.IntegerField()

    # basic account capabilities (they require additional permissions on resources)
    READ = 1            # can view files and folders from this team
                        # unsetting this must delete the member and all symlinks
    WRITE = 2           # can create or delete resources belonging to this team
    SHARE = 4           # can share files and folders belonging to this team
    INVITE = 8 | SHARE  # invite new team members via email or phone; invited
                        # users can only have a subset of the caller's permissions,
                        # defaulting to mask & 7 (inherit basics)

    # admin permissions
    # READ_ALL == (un)set READ on team root
    # WRITE_ANY == (un)set WRITE_ONLY on team root
    # SHARE_ANY = (un)set SHARE_ONLY on team root

    # additional user-grantable permissions
    #USER_PERM = 16
    #USER_PERM = 32
    #USER_PERM = 64
    #USER_PERM = 128
    #USER_PERM = 256
    #USER_PERM = 512
    #USER_PERM = 1024
    #USER_PERM = 2048
    #USER_PERM = 4096
    #USER_PERM = 8192
    #USER_PERM = 16384
    #USER_PERM = 1<<15
    #USER_PERM = 1<<16
    #USER_PERM = 1<<17
    #USER_PERM = 1<<18
    #USER_PERM = 1<<19
    #USER_PERM = 1<<20

    MANAGE_BILLING = 1<<21    # can view and update billing information
    MANAGE_USERS = (1<<23)-1  # change user permissions and delete users:
                              # since having this means you can grant yourself
                              # everything else, we treat it as an is_admin flag,

    # RESERVED for administrative purposes; users may not gain these permissions
    # through normal means, only via code or direct db manipulation
    #ADMIN_PERM = 1<<23
    #ADMIN_PERM = 1<<24
    #ADMIN_PERM = 1<<25
    #ADMIN_PERM = 1<<26
    #ADMIN_PERM = 1<<27
    #ADMIN_PERM = 1<<28
    #ADMIN_PERM = 1<<29
    #ADMIN_PERM = 1<<30

    def has_permission(self, value):
        return (self.mask & value) == value


class Team(models.Model):
    class Meta:
        db_table = 'team'

    name = models.CharField(max_length=200)
    root = models.ForeignKey('Resource', on_delete=models.RESTRICT)  # team root folder

    # quotas; exceeding these can cause operations to fail
    member_quota = models.IntegerField()  # this is 1 for individual accounts
    resource_quota = models.IntegerField()
    storage_quota = models.BigIntegerField()

    # running sum for quota values
    used_members = models.IntegerField(default=0)
    used_resources = models.IntegerField(default=1)  # team root folder must always exist
    used_storage = models.BigIntegerField(default=0)

    # for every team, at least one member must have the MANAGE_USERS permission
    # at any given time; an operation against a member (remove, change perms)
    # will not succeed if by doing so, this invariant will be broken
    # ---
    # bill is sent to all members that have MANAGE_BILLING but not MANAGE_USERS
    # if there are none, or if overdue, it's sent to all members that have MANAGE_USERS
    # if overdue, all users that have MANAGE_BILLING get a notification when logging in
    members = models.ManyToManyField(User,  # list of members
                                     through=Membership,
                                     related_name='teams')
    # resources = [Resource] defined by resource.py
