import random

from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, \
    PermissionsMixin
from django.core.validators import validate_email


PASSWORD_ALPHABET = ('abcdefghijklmnopqrstuvwxyz'
                     'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
                     '0123456789!@#$%^&*()-=_+'
                     '[]{};\'\\:"|,./<>?~ ')
PASSWORD_LENGTH = 16


class UserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        """Creates and saves a new user."""
        if not email:
            raise ValueError('Users must have an email address')
        # Raises ValidationError exception if invalid
        validate_email(email)

        if not password:
            password = User.generate_password()

        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        # using=self._db is just required for supporting multiple databases
        # which is not our case initially but it's good practice to keep it.
        user.save(using=self._db)

        return user

    def create_superuser(self, email, password):
        """Creates and saves a new super user"""
        user = self.create_user(email, password)
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)

        return user


class User(AbstractBaseUser, PermissionsMixin):
    """Custom user model that supports using email instead of username"""
    email = models.EmailField(max_length=255, unique=True)
    name = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = 'email'

    # E.164 without dial out code (e.g. 011 in US and Canada and 00 otherwhere)
    phone = models.CharField(max_length=15, unique=True, null=True)

    # teams = [Team] - defined on Team with a reverse here
    def summary(self):
        return {
            'id': self.id,
            'email': self.email
        }

    @classmethod
    def generate_password(cls):
        return ''.join(random.choices(PASSWORD_ALPHABET, k=PASSWORD_LENGTH))
