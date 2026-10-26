import contextlib
import sys

# Upstream prints its startup banner during import. Preserve it on stderr;
# never filter the MCP stream by JSON key order or merge stderr into it.
with contextlib.redirect_stdout(sys.stderr):
    from SystemSage.server import serve

serve()
