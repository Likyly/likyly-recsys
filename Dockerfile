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
COPY requirements.txt /srv/

RUN python -m venv /srv/venv-docker

# install the cpu-only torch (or any other torch-related packages)
# you might modify it to install another version
RUN /srv/venv-docker/bin/pip install --no-cache-dir --upgrade pip setuptools wheel
# --no-cache-dir: pip's download cache would stay in this layer (hundreds of MB on the droplet's disk)
RUN /srv/venv-docker/bin/pip install --no-cache-dir torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu

RUN /srv/venv-docker/bin/pip install --no-cache-dir --upgrade pip
RUN /srv/venv-docker/bin/pip install -r requirements.txt --no-cache-dir


COPY . /srv

EXPOSE 6061

ENV PATH="/srv/venv-docker/bin:$PATH"

WORKDIR /srv/application/api
CMD ["/srv/venv-docker/bin/python", "app.py"]
