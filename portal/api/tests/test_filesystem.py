from django.contrib.auth import get_user_model
from django.test import TestCase, tag
import requests
from rest_framework.test import APIClient

from ..models import Team, Resource, Symlink, Membership
User = get_user_model()
api = APIClient()


@tag('fs')
class FilesystemTests(TestCase):
    '''test helpers'''
    def add_team(self, name, owner=None):
        root = Resource.objects.create(name=f'{name} root folder',
                                       kind='folder',
                                       folder=None)
        team = Team.objects.create(name=name,
                                   member_quota=1,
                                   storage_quota=1e9,
                                   resource_quota=1e5,
                                   root=root)
        if owner:
            Membership.objects.create(user=owner, team=team, mask=Membership.MANAGE_USERS)
        return team

    def add_user(self, name, team=None, mask=0):
        user = User.objects.create(email=name + '@padcaster.com',
                                   password='blahblah')
        if team:
            Membership.objects.create(user=user, team=team, mask=mask)
        else:
            team = self.add_team(name + '\'s team', user)

        folder = Resource.objects.create(name=f'{name}\'s private folder',
                                         kind='folder',
                                         folder=team.root)
        Symlink.objects.create(resource=folder,
                               user=user,
                               mask=Symlink.OWNER)
        return user, folder

    def setUp(self):
        self.bob, self.bobs_folder = self.add_user('bob')
        self.alice, self.alices_folder = self.add_user('alice')

    # test folder list
    def test_bob_can_access_his_own_private_folder(self):
        api.force_login(self.bob)
        res = api.get('/api/v0/files/')

        # there's a symlink to bob's private folder owned by bob
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['entries'][0]['id'], self.bobs_folder.id)
        private = str(self.bobs_folder.id)
        self.assertEqual(data['next'], private)

        # pagination on /files
        res = api.get(f'/api/v0/files/?cursor={private}')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data['entries']), 0)
        self.assertEqual(data['next'], private)

        # iter on /files/id
        res = api.get(f'/api/v0/files/{private}/')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data['name'], 'bob\'s private folder')
        self.assertEqual(data['folder'], self.bob.teams.first().root.id)
        children = data['children']
        self.assertEqual(len(children['entries']), 0)
        self.assertEqual(children['next'], '0')  # no cursor provided and no files found

    # list folder helper - do not use in the above tests
    def list_folder(self, folder=None, cursor=None):
        route = '/api/v0/files/'
        if folder:
            route += f'{folder}/'
        if cursor:
            route += f'?cursor={cursor}'
        res = api.get(route)
        self.assertEqual(res.status_code, 200)
        data = res.json()['children']
        for resource in data['entries']:
            yield resource
        if cursor != data['next']:
            yield from self.list_folder(folder, data['next'])

    # test create folders
    def test_alice_can_create_folders(self):
        api.force_login(self.alice)
        data = self.create_folder('new folder', self.alices_folder.id)

        # alice can list the folder
        self.assertEqual(api.get(f"/api/v0/files/{data['id']}/").status_code, 200)

    # create folder helper - do not use in the above tests
    def create_folder(self, name, parent):
        res = api.put(f'/api/v0/files/{parent}/', dict(name=name, size=0))
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data['name'], name)
        self.assertEqual(data['kind'], 'folder')
        return data

    # test resource deletion
    def test_alice_can_delete_folders(self):
        api.force_login(self.alice)
        # create the following folder structure in alice's private folder:
        # parent/child/grandchild
        parent = self.create_folder('to be deleted', self.alices_folder.id)['id']
        child = self.create_folder('child', parent)['id']
        grandchild = self.create_folder('child', child)['id']

        # parent contains the child but not grandchild
        children = list(self.list_folder(parent))
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]['id'], child)

        # child contains grandchild
        children = list(self.list_folder(child))
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]['id'], grandchild)

        # delete grandchild
        res = api.delete(f'/api/v0/files/{grandchild}/')
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.json(), {'detail': 'Resource deleted'})
        self.assertEqual(api.get(f'/api/v0/files/{grandchild}/').status_code, 404)

        # child is empty but accessible
        children = list(self.list_folder(child))
        self.assertEqual(len(children), 0)

        # delete parent
        res = api.delete(f'/api/v0/files/{parent}/')
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.json(), {'detail': 'Resource deleted'})

        # alice can no longer access the folder
        self.assertEqual(api.get(f'/api/v0/files/{parent}/').status_code, 404)

        # the subfolder was deleted as well
        self.assertEqual(api.get(f'/api/v0/files/{child}/').status_code, 404)

    # delete resource helper - do not use in the above tests
    def delete_resource(self, resource):
        '''deletes an existing file or folder; not to be used by the above as it
        assumes that some of its assertions are valid'''
        res = api.delete(f'/api/v0/files/{resource}/')
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.json(), {'detail': 'Resource deleted'})
        self.assertEqual(api.get(f'/api/v0/files/{resource}/').status_code, 404)

    @tag('integration')
    def test_alice_can_create_files(self):
        '''this currently uploads a file and then deletes it; ideally we should
        mock this at some point in the future'''
        test_data = 'hello world'.encode('utf-8')

        api.force_login(self.alice)
        res = api.put(f'/api/v0/files/{self.alices_folder.id}/', {
            'name': 'new file',
            'size': len(test_data)
        })
        self.assertEqual(res.status_code, 202)
        data = res.json()
        parts = data['parts']

        # this is a small file so it is composed out of 1 part
        self.assertEqual(len(parts), 1)
        part = parts[0]
        self.assertEqual(part['start'], 0)
        self.assertEqual(part['end'], len(test_data))

        # part urls are always s3 https
        self.assertTrue(part['url'].startswith('https://'))
        self.assertIn('.s3.amazonaws.com/', part['url'])

        # commit url is local
        self.assertTrue(data['commit'].startswith('http://testserver'))

        # do a put to s3, s3 should return an etag
        res = requests.put(part['url'], data=test_data)
        self.assertEqual(res.status_code, 200)

        # publish etag to local commit url
        res = api.post(data['commit'], {'parts': [res.headers['ETag']]}, format='json')
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data['name'], 'new file')
        self.assertEqual(data['kind'], 'file')  # files are not identified on upload
        self.assertEqual(data['folder'], self.alices_folder.id)
        self.assertIn('.s3.amazonaws.com/', data['original']['url'])

        # pull file and compare contents
        res = requests.get(data['original']['url'])
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, test_data)

        # alice can delete this file
        file = data['id']
        self.assertEqual(api.get(f'/api/v0/files/{file}/').status_code, 200)
        res = api.delete(f'/api/v0/files/{file}/')
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.json(), {'detail': 'Resource deleted'})
        self.assertEqual(api.get(f'/api/v0/files/{file}/').status_code, 404)

    def test_bob_cant_access_his_team_root_folder(self):
        api.force_login(self.bob)
        root = self.bobs_folder.folder
        self.assertEqual(root, self.bob.teams.first().root)
        self.assertEqual(api.get(f'/api/v0/files/{root.id}/').status_code, 403)

    def test_alice_cant_read_bobs_private_folder(self):
        # no user
        api.logout()
        self.assertEqual(api.get('/api/v0/files/').status_code, 401)

        api.force_login(self.alice)

        # alice can't access bob's folder
        self.assertEqual(api.get(f'/api/v0/files/{self.bobs_folder.id}/').status_code, 403)

        # but alice can access her files
        self.assertEqual(api.get('/api/v0/files/').status_code, 200)
        self.assertEqual(api.get(f'/api/v0/files/{self.alices_folder.id}/').status_code, 200)

    '''
    todo: store usage on resource instead of team::
      somewhat simplifies quota handling but more importantly gives options to:
      * refuse copy if too many resources are involved
      * quick way to show folder size in ui

    cases for 100% coverage (feel free to add tests or add to list):
    * can invite via user id (user not on team but client can know about the
                              invited user's id from a different team they
                              collaborate on)
    * can invite user via email (invited user not on team but email already in db)
    * can invite user via email (invited user does not exist)
    * can copy file
    * can copy folder with filesystem tree
    * check permissions after copying (none + inherited)
    * check quotas after copying
    * can move file
    * can move folder
    * check permissions after moving (existing + inherited)
    * check quotas after moving
    '''
