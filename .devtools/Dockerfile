# Home Assistant tracks the newest Python closely -- current releases need 3.14. This image
# exists so the test suite can run against a real Home Assistant without installing another
# interpreter on the host.
# Pinned to amd64 deliberately. On an arm64 Docker Desktop of this vintage the
# cryptography wheel's OpenSSL backend dies with SIGILL -- the VM advertises CPU
# features it does not fully support. Emulated amd64 is slower but correct.
FROM --platform=linux/amd64 python:3.14

WORKDIR /work
COPY requirements-test.txt /tmp/requirements-test.txt
RUN pip install --no-cache-dir --root-user-action=ignore -r /tmp/requirements-test.txt

# aiodisklavier is mounted from a sibling checkout and installed at run time, so the
# integration is always tested against the library source rather than a published release.
CMD ["pytest"]
