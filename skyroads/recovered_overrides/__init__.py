"""Address-keyed MANUAL CPUless overrides.

Drop a hand-written ``func_CCCC_IIII.py`` here to override the GENERATED body
for that address in the standalone corpus. build_recovered.py layers
these on top of skyroads/recovered/ after generation, and the manifest
records them as ``manual-cpuless-override``. An override must match the
generated module's interface: ``func_cccc_iiii(mem[, plat], *, <inputs>) ->
(outputs, compat)``, and it must import nothing outside the recovered package
(the purity lint enforces this). Empty today -- the generator covers all 182
runtime-reachable functions; this is the seam for a fix the generator cannot
yet produce.
"""
