# pyaes wheel

`pyaes-1.6.1-py3-none-any.whl` is the unmodified, pure-Python dependency used by Telethon, packaged as a wheel because Chaquopy's Android dependency installer requires wheels. Its MIT license is included at `pyaes-1.6.1.dist-info/licenses/LICENSE.txt` inside the wheel.

Source: [pyaes 1.6.1 on PyPI](https://pypi.org/project/pyaes/1.6.1/).

Source archive SHA-256: `02c1b1405c38d3c370b085fb952dd8bea3fadcee6411ad99f312cc129c536d8f`.

Bundled wheel SHA-256: `33f9a5d3dfdc4c40aa4fbe864b42ed37f5b106da168ee5c7f454dedc016140b0`.

To rebuild from the project root:

```bash
venv/bin/python -m pip wheel --no-deps --no-cache-dir pyaes==1.6.1 --wheel-dir android/wheels
```

Wheel archive timestamps can change the wheel hash when rebuilding. The Python sources are not modified.
