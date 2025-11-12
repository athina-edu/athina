FROM python:3.14-slim

ARG DEBIAN_FRONTEND=noninteractive

# Things that our test scripts use and need to have installed
RUN apt-get update && apt-get -y install \
    git \
    docker.io \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ADD . /code
WORKDIR /code
RUN pip3 install pip
RUN pip3 install .

ENTRYPOINT ["athina-cli"]
CMD [""]
