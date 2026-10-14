"""Query local readiness without redirects or routing-provider requests."""

import sys
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(
            Request("http://127.0.0.1:8000/health/ready"), timeout=5
        ) as response:
            if response.status != 200:
                sys.exit(1)
    except OSError:
        sys.exit(1)


if __name__ == "__main__":
    main()
