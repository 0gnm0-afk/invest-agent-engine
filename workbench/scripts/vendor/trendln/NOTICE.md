# trendln 0.1.18

Unmodified Python module and MIT license from the official PyPI wheel.
Upstream: https://github.com/GregoryMorse/trendln
Release: https://pypi.org/project/trendln/0.1.18/
Wheel SHA-256: `7d2982ef7de565494580a8bcca23cdd4ed239048b45a4861b88dbe793bc0a5e0`
Module SHA-256: `05928717a135ae6235ae697e0d519bdf54ab31d7b36f35824d41d7a13a763ec7`

Vendored to make local/offline dashboard startup reproducible without modifying
the shared engine environment. The adapter uses METHOD_NAIVE and
METHOD_NSQUREDLOGN; these paths require the existing numpy and pandas runtime.
Unused plotting/numerical differentiation/Hough paths are not invoked and their
optional findiff/scikit-image dependencies are not installed by this integration.
Update the pinned module, license, adapter version and tests together.
