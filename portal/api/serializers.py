# these are only used for admin-only routes
from django.contrib.auth import get_user_model
from rest_framework.serializers import ModelSerializer

from .models import Article, Category, Subcategory
from .utils import RelatedFieldMixin


class UserSerializer(ModelSerializer, RelatedFieldMixin):
    class Meta:
        model = get_user_model()
        fields = ['id', 'date_joined', 'email', 'first_name', 'groups',
                  'is_active', 'last_login', 'last_name', 'username']


class CategorySerializer(ModelSerializer, RelatedFieldMixin):
    class Meta:
        model = Category
        fields = ['id', 'title', 'slug', 'priority', 'published']


class SubcategorySerializer(ModelSerializer, RelatedFieldMixin):
    category = CategorySerializer.RelatedField()

    class Meta:
        model = Subcategory
        fields = ['id', 'category', 'title', 'slug', 'priority', 'published']
        depth = 1


class ArticleSerializer(ModelSerializer, RelatedFieldMixin):
    subcategory = SubcategorySerializer.RelatedField()

    class Meta:
        model = Article
        fields = ['id', 'subcategory', 'slug', 'priority',
                  'published', 'title', 'contents', 'thumbnail']
        depth = 2
