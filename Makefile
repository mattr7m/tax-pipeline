TAX ?= localhost/tax-processor:latest
ENGINE ?= podman

.PHONY: build push all

build:
	$(ENGINE) build -t $(TAX) -f Containerfile .

push:
	$(ENGINE) push $(TAX)

all: build push
