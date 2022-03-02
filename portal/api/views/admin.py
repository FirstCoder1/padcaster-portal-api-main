from django.contrib.auth import get_user_model
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAdminUser
from rest_framework.viewsets import ModelViewSet

from ..utils import mount, authorize
from ..models import Category, Subcategory, Article
from ..serializers import CategorySerializer, SubcategorySerializer, \
    ArticleSerializer, UserSerializer


@mount('admin/users')
@authorize(IsAdminUser)
class Users(ModelViewSet):
    '''User management'''
    # list
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['username', 'email']
    ordering_fields = ['username', 'email', 'phone', 'first_name', 'last_name',
                       'date_joined', 'last_login']
    ordering = ['date_joined']
    queryset = get_user_model().objects.all()
    serializer_class = UserSerializer


@mount('admin/categories')
@authorize(IsAdminUser)
class Categories(ModelViewSet):
    '''Digital toolkit article subcategory management'''
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['title', 'slug']
    ordering_fields = ['title', 'slug', 'priority']
    ordering = ['priority']
    queryset = Category.objects.all()
    serializer_class = CategorySerializer


@mount('admin/subcategories')
@authorize(IsAdminUser)
class Subcategories(ModelViewSet):
    '''Digital toolkit article category management'''
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['title', 'slug']
    ordering_fields = ['title', 'slug', 'priority']
    ordering = ['priority']
    queryset = Subcategory.objects.all()
    serializer_class = SubcategorySerializer


@mount('admin/articles')
@authorize(IsAdminUser)
class Articles(ModelViewSet):
    '''Digital toolkit article category management'''
    filter_backends = [SearchFilter, OrderingFilter]
    search_fields = ['title', 'slug']
    ordering_fields = ['title', 'slug', 'priority']
    ordering = ['priority']
    queryset = Article.objects.all()
    serializer_class = ArticleSerializer
