TAX ?= localhost/tax-processor:latest

.PHONY: build push all

build:
	podman build -t $(TAX) -f Containerfile .

push:
	podman push $(TAX)

all: build push
