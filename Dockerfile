FROM ubuntu:20.04

ARG DEBIAN_FRONTEND=noninteractive

# Things that our test scripts use and need to have installed
RUN apt-get update && echo "37" | apt-get -y install keyboard-configuration \
&& apt-get -y install python3 python3-pip git-core docker.io firejail ca-certificates

ADD . /code
WORKDIR /code
RUN pip3 install pip
RUN pip3 install .

ENTRYPOINT ["athina-cli"]
CMD [""]
