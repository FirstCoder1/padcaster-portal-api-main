from django.db import models


class Category(models.Model):
    class Meta:
        db_table = 'category'

    title = models.CharField(max_length=200)  # user readable name
    slug = models.SlugField(max_length=50, unique=True)  # url slug, unique
    priority = models.IntegerField(default=0)  # display priority
    published = models.BooleanField(default=False)  # visible?

    def __str__(self):
        return self.slug


class Subcategory(models.Model):
    class Meta:
        db_table = 'subcategory'

    category = models.ForeignKey(Category,
                                 on_delete=models.CASCADE,
                                 related_name='subcategories')
    title = models.CharField(max_length=200)  # user readable name
    slug = models.SlugField(max_length=50, unique=True)  # url slug, unique
    priority = models.IntegerField(default=0)  # display priority in category
    published = models.BooleanField(default=False)  # visible?

    def __str__(self):
        return self.slug


class Article(models.Model):
    class Meta:
        db_table = 'article'

    subcategory = models.ForeignKey(Subcategory,
                                    on_delete=models.SET_NULL,
                                    related_name='articles',
                                    null=True)
    slug = models.SlugField(max_length=200, unique=True)  # url slug, unique
    priority = models.IntegerField(default=0)  # display priority in subcategory
    published = models.BooleanField(default=False)  # visible?

    title = models.CharField(max_length=200)  # user readable name (text)
    contents = models.TextField()  # article contents (html)
    thumbnails = models.ManyToManyField('Resource',
                                        related_name='+',
                                        db_table='article_resource_link')  # stored in 'articles' folder

    def __str__(self):
        return self.slug
