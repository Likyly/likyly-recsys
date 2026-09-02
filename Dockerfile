FROM python:3.11-slim-bookworm

RUN apt update && apt install -y \
    git \
    build-essential \
    python3-dev \
    python3-pip \
    python3-wheel \
    python3-setuptools \
    curl \
    && apt clean \
    && rm -rf /var/lib/apt/lists/*


WORKDIR /srv
COPY requirements-docker.txt /srv/

RUN python -m venv /srv/venv-docker

# install the cpu-only torch (or any other torch-related packages)
# you might modify it to install another version
RUN /srv/venv-docker/bin/pip install --upgrade pip setuptools wheel
RUN /srv/venv-docker/bin/pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu

RUN /srv/venv-docker/bin/pip install --upgrade pip
RUN /srv/venv-docker/bin/pip install -r requirements-docker.txt --no-cache-dir


COPY . /srv

EXPOSE 6061

ENV PATH="/srv/venv-docker/bin:$PATH"

WORKDIR /srv/application/api
CMD ["/srv/venv-docker/bin/python", "app.py"]
