# Note: I usually  prefer alpine, since is the most light-weight version for images.
# But in this case due to libvips limitations with alpine we are using a more robust image.
FROM python:3.10-slim-buster

# set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV APP_WORKDIR=/portal

COPY ./requirements /requirements

# Update the registry before we add anything but don't store the registry index on our container
RUN apt-get update && apt-get install -y postgresql-client

# Minimum footprint possible
RUN apt-get update && \
  apt update && \
  apt-get install -y --no-install-recommends \
  gcc libc-dev python-psycopg2 libvips \
  libpq-dev python3-dev libvips-dev libmagic-dev

RUN pip install -Ur /requirements/base.txt
RUN pip install -Ur /requirements/debug.txt

RUN mkdir -p $APP_WORKDIR
WORKDIR $APP_WORKDIR
COPY ./portal $APP_WORKDIR

# Best practice, for security purposes, docker service is running as a least-priviled user
RUN adduser user
RUN chown -R user:user $APP_WORKDIR
USER user
