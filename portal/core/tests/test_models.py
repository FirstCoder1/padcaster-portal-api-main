from django.test import TestCase
# Note: You can import the user model directly from the models but this is
# not recommended with django because at some point in the project you
# may want to change what your user model is and if everything is using
# the get_user_model() function then that's really easy to do.
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError


def sample_user(email='test@test.com', password='testPassword123'):
    """Create a sample user"""
    return get_user_model().objects.create_user(email, password)


class ModelTests(TestCase):

    def test_create_user_with_email_successful(self):
        """Test creating a new user with an email is successfull"""
        email = 'test@test.com'
        password = 'testPassword123'
        user = sample_user(email, password)

        self.assertEqual(user.email, email)
        self.assertTrue(user.check_password(password))

    def test_new_user_email_normalized(self):
        """Test the email for a new user is normalized"""
        email = 'testuser@TEST.COM'
        user = sample_user(email, 'testPassword123')

        self.assertEqual(user.email, email.lower())

    def test_new_user_no_email(self):
        """Test creating user with no email raises error"""
        with self.assertRaises(ValueError):
            get_user_model().objects.create_user(None, 'testPassword123')

    def test_new_user_invalid_email(self):
        """Test creating user with invalid email raises error"""
        with self.assertRaises(ValidationError):
            get_user_model().objects.create_user('test', 'testPassword123')

    def test_create_new_superuser(self):
        """Test creating a new superuser"""
        user = get_user_model().objects.create_superuser(
            'test@test.com',
            'testPassword123'
        )

        self.assertTrue(user.is_superuser)
        self.assertTrue(user.is_staff)
