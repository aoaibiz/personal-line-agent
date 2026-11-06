# Third-party notices

The optional capability adapter depends on @evex-dev/linejs 3.6.4 (MIT), with source reference c0f4a21a374af677b1f7d1955a1f228e80779643. The Python album protocol contracts were informed by the same project. Its license is preserved in docs/upgrade-20261105/LINEJS-LICENSE.txt and the npm package distribution. Dependencies and integrity hashes are locked in adapters/linejs/package-lock.json; transitive licenses remain in their packages. The Thrift Node dependency is overridden to 0.23.0 to avoid published vulnerabilities in the upstream range; Python Thrift is pinned to 0.24.0.

No upstream authentication file-storage or generic raw RPC/MCP dispatcher is exposed. Existing personal-line-agent owner authority and encrypted persistence remain the boundary.

Own-profile cover wire contracts are informed by DeachSword/CHRLINE commit3ee320668e2da6fc04b50e2054b880e86d23434d (BSD-3-Clause, copyright2021 DeachSword). Its complete notice is retained in adapters/linejs/LICENSE.CHRLINE.txt and shipped in source, wheel and managed runtime. This does not imply upstream endorsement or current write compatibility.
