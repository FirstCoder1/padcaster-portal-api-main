# padcaster-portal-api

Padcaster Portal RESTful API

---

## Setup

### Prerequisites

- [VS Code](https://code.visualstudio.com/) is recommended for working on this project.

---

### Local Setup - Docker (Recommended)

#### Installation

- Install and setup [Docker](https://www.docker.com/)

#### Run Server

```bash
docker-compose up
```

#### Create an admin user in your local environment

```bash
docker-compose run app sh -c "python manage.py createsuperuser"
```

#### Run Tests

```bash
docker-compose run app sh -c "python manage.py test && flake8"
```

---

### Local Setup - Manual installation

#### Local prerequisites

- Install and setup [Python](https://www.python.org/)
- Install and setup [PostgreSQL](https://www.postgresql.org/)
- Install and setup [libvips](https://github.com/libvips/libvips)

#### Setup Local Variables

In order to connect to your postgres instance you need to setup the following environment variables:

- DB_HOST - The host of the database server, if local probably it is 127.0.0.1
- DB_NAME - The database name
- DB_USER - The database username that can access to the DB_NAME database
- DB_PASSWORD - the DB_USER password

#### Install app prerequisites and migrations

```bash
python setup.py
```

Run migrations:

```bash
cd portal
python manage.py migrate
```

#### Create superuser

```bash
cd portal
python manage.py createsuperuser --email admin@example.com
```

#### Run the server locally

```bash
cd portal
python manage.py runserver
```

Open [http://localhost:8000](http://localhost:8000) with your browser to see the result.

In VSCode, start the debugger to run the development server with interactive debugging.

---

## App Architecture

### Core App

The core app holds all of the central code, everything that is important to the rest of the sub-apps that we create in this system.

Anything that is shared between one or more apps like the migrations, models, admin setup, core tests, so it's very clear where the central point of all these things is.

TODO: Describe other apps

---

### Code Management and Deployment

##### Contributing Code

Please follow this protocol to contribute code from the Git CLI:

- `git checkout main`
- `git pull`
- `git checkout -b`_`[branch]`_
- `git push -u origin`_`[branch]`_
- _do work and commits here_
- `git checkout main`
- `git pull`
- `git checkout`_`[branch]`_
- `git rebase`_`[-i]`_`main`
- _fix any conflicts_
- `git push`

When your work is ready for review, create a pull request on [Github](https://github.com/rehashstudio/padcaster-portal-api/).

##### Branch Naming

The following branch names are reserved. Do not commit directly to these branches.

- `main`
  - The main development branch.
- `test`
  - Merging to this branch triggers the deployment pipeline.
- `stable/YYYY-MM-DD-V`
  - Archive of production branch deployed on _`YYYY-MM-DD`_, where _`V`_ is the version number, if multiple deployments happened that day. These branches should be made for every deployment for quick reversions.
- `archive/YYYY-MM-DD`
  - Archive of unstable branch made on _`YYYY-MM-DD`_. Use this to preserve experimental or unused code.

When creating branches to perform work, please use the following naming conventions (`-ii` represents your first and last initials):

- `PAD-###`_`-ii`_
  - When your work addresses a ticket from Jira, use the ticket ID as the branch name. If multiple developers may be working on the ticket, append your initials. For example, `PAD-108-ss`. **Prefer this format.** If a ticket doesn't exist for your work, please request one from your PM to work against. Fall back to another format if your PM is unavailable.
- `feature/ii-description`
  - Use this format when adding a new feature not associated with a ticket.
- `experiment/ii-description`
  - Use the `experiment` prefix for sandbox branches with breaking or destructive changes. Branches prefixed with `experiment` will not be considered for merging into main. If an experimental branch is successful, create a new `feature` branch from it for PR.
- `hotfix/ii`
  - Use the `hotfix` prefix and your initials for time-constrained updates, or updates which may address multiple high-priority tickets.
- `update/ii-description`
  - Use this format when updating existing functionality not associated with a ticket or a bug.

##### Code Review

When reviewing code, please do the following:

- `git pull`
- `git checkout`_`[branch]`_
- _build, run, test_
- _On [Github](https://github.com/rehashstudio/padcaster-portal-api/)_
  - _fail_
    - _Reject PR, dev works/commits and re-PRs_
    - _Assign ticket to dev_
  - _pass_
    - _Approve_
    - _Merge_
    - _Delete feature branch_

##### Deployment

To deploy source code to staging, merge `main` into `test`:

- `git checkout main`
- `git pull`
- `git checkout test`
- `git pull`
- `git merge main -s resolve`
- `git push`

This will trigger a deployment in AWS CodePipeline. If the deployment is successful, review the staging instance at [TBD](https://tbd.com).
