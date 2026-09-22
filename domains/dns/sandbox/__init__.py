"""Programs the DNS fixture copies into the lab and runs *inside the sandbox*.

Nothing in this package is run by the harness process. The fixture provider
reads the source files with :mod:`importlib.resources` and ships them to the
lab as :class:`~harness.core.ports.LabFile` bytes; they execute under the lab
image's own ``python3``. Keep them standard-library only and compatible with
the Python the lab image carries (3.8+).
"""
