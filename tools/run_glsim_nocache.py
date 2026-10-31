"""Launch GLSim with its class caches disabled (local workaround, tests only).

GLSim 0.29.2 re-allocates a cached contract class for every deployment after the
first one, and that path raises "class is not marked for usage within storage"
(reproducible with the boilerplate's FootballBets too). Disabling the two caches
makes every deployment/schema request load the contract class from source, which is
the path that works. This changes nothing about contract execution or consensus.

    python tools/run_glsim_nocache.py --port 4000 --validators 5 --no-browser
"""

import glsim.engine as engine


class _NoCache(dict):
    def get(self, key, default=None):
        return default

    def __setitem__(self, key, value):
        pass


_original_init = engine.SimEngine.__init__


def _init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    self._class_cache = _NoCache()
    self._code_hash_cache = _NoCache()


engine.SimEngine.__init__ = _init

if __name__ == "__main__":
    from glsim.__main__ import main

    main()
