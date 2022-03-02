from collections import OrderedDict
from rest_framework.routers import DefaultRouter
from rest_framework.serializers import RelatedField as _RelatedField

import boto3

router = DefaultRouter()
s3 = boto3.client('s3')

def mount(route, *args, **kwargs):
    def wrapper(viewset):
        router.register(route, viewset, *args, **kwargs)
        return viewset
    return wrapper


def authorize(*base, **actions):
    '''Applies *base permissions to view, extending them with actions[action]
    and actions['detail'] or actions['generic'] depending on request context'''
    def get_permissions(self):
        permission_classes = list(base)
        for extra in (
            actions.get(self.action, []),
            actions.get('detail' if self.detail else 'generic', [])
        ):
            if isinstance(extra, list):
                permission_classes.extend(extra)
            else:
                permission_classes.append(extra)
        return [permission() for permission in permission_classes]

    def wrapper(viewset):
        viewset.get_permissions = get_permissions
        return viewset
    return wrapper


class RelatedFieldMixin:
    @classmethod
    def RelatedField(cls, *args, **kwargs):
        # TODO: figure out a way to expose the class directly (needs `cls`)
        class RelatedFieldWithCustomRepresentation(_RelatedField):
            queryset = cls.Meta.model.objects.all()

            def get_choices(self, cutoff=None):
                # https://github.com/CenterForOpenScience/osf.io/commit/8fb7a813b651db881fb05c28bbe781601e58c83a
                # https://github.com/encode/django-rest-framework/issues/5104
                # https://github.com/encode/django-rest-framework/issues/5141
                q = self.get_queryset()
                if cutoff is not None:
                    q = q[:cutoff]
                return OrderedDict([(i.id, self.display_value(i)) for i in q])

            def to_representation(self, user):
                return cls(user, context=self.context).data

            def to_internal_value(self, _id):
                return self.get_queryset().get(id=_id)

        return RelatedFieldWithCustomRepresentation(*args, **kwargs)


class RawQueryBuilder:
    '''
    Converts the output of a raw query into a populated model.

    Args:
        model: base model class for this query (e.g. result)
        alias: aliased name for the table that must contain all of the base
               model's columns in the queryset
    '''
    def __init__(self, model, alias):
        self._model = model
        self._cols = []
        self._cache = {}
        self._links = {}
        self._offset = self._add_cols(model, alias)

    @property
    def cols(self):
        '''returns a sql fragment that contains the select statement's body'''
        return ', '.join(self._cols)

    def _add_cols(self, model, alias):
        '''adds all the concrete fields of model to the queryset, from a
        explicitly aliased table'''
        for field in model._meta.concrete_fields:
            self._cols.append(f'{alias}.{field.attname}')
        return len(self._cols)

    def select_related(self, attname, alias):
        '''populates model.attname by adding all its concrete cols to this
        query from the 'alias' table;

        if 'alias' can alternatively be an instance of the field, in which case
        it will be used as the static value for all rows and no additional cols
        will be selected
        '''
        for field in self._model._meta.concrete_fields:
            if field.attname == attname:
                break
        else:
            raise KeyError(f'Column {attname} does not exist on {self._model._meta.db_table}')

        model = field.related_model  # pylint: disable=undefined-loop-variable
        if isinstance(alias, model):
            self._cache[attname] = alias
        else:
            self._links[attname] = model, len(self._cols), self._add_cols(model, alias)

    def row_to_model(self, row, db='default'):
        '''Returns the model associated with a row, populating all fields
        mentioned in select_related'''
        result = self._model.from_db(db, None, row[:self._offset])

        # populate model cache
        for field in result._meta.fields:
            if field.attname in self._cache:
                value = self._cache[field.attname]
            elif field.attname in self._links:
                model, start, end = self._links[field.attname]
                value = model.from_db(db, None, row[start:end])
            else:
                continue
            field.set_cached_value(result, value)

        return result
