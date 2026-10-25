# SPDX-License-Identifier: GPL-2.0-or-later
"""The certificates every HTTPS request of AMDLSS trusts: the update check, the downloads, covers and reports.

Python's ssl on Windows sees only the root CAs already in the Windows store. Windows fetches most roots from Windows
Update the first time a Windows program needs one, which Python never triggers, so on a PC that never met GitHub's
current roots (USERTrust ECC for api.github.com, ISRG Root X1 for the release downloads) every request failed with
'unable to get local issuer certificate'. context() adds Mozilla's root list from certifi, which the release builds
carry, to the system store; the system store stays, so a root a company or an antivirus added still works. From
source without certifi it is the plain system store, as before."""
import ssl
import urllib.request

try:
    import certifi
except ImportError:   # from source AMDLSS needs only the standard library
    certifi = None


def context():
    """The default context (the system store) plus certifi's roots when certifi is there."""
    trusted = ssl.create_default_context()
    if certifi is not None:
        trusted.load_verify_locations(cafile=certifi.where())
    return trusted


def install():
    """Every later urllib.request.urlopen() in this process verifies with context(). main() calls it first."""
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=context())))
