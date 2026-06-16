"""
XTF (eXtended Triton Format) export for recorded sidescan bags.

Submodules:

* :mod:`bag_analysis.xtf.geo` -- WGS84 ECEF <-> geodetic conversion and
  ECEF orientation -> local (NED) heading/pitch/roll extraction, used to
  turn a ``earth`` (ECEF) frame TF lookup into the lat/lon and attitude
  an XTF ping header needs.
* :mod:`bag_analysis.xtf.writer` -- a streaming binary writer for the
  XTF file header, channel-info blocks, and per-ping packets.
"""
