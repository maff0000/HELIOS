"""The running HELIOS service.

Everything else in this package is a library: contracts, strategies, chains,
the publication boundary. This is the part that *runs* — the loop that turns an
ordered feed of HERMES facts into a continuous stream of published strategy
state, and the health, shutdown and configuration behaviour a container needs.

* :mod:`helios.runtime.config` — what the service must be told, and the rule
  for which settings it refuses to start without.
* :mod:`helios.runtime.feed` — the ordered, multi-timeframe market-fact feed,
  and the instants a replay evaluates at.
* :mod:`helios.runtime.service` — the evaluation loop, clean shutdown, and the
  loud startup validation that precedes both.
* :mod:`helios.runtime.health` — health and readiness with **no listening
  socket**: a status document the service writes and a probe reads.

Run it with ``python3 -m helios.runtime``; probe it with
``python3 -m helios.runtime.health``. See ``docs/RUNTIME.md``.

Why this module re-exports nothing
----------------------------------
:mod:`helios.config` is the single configuration entry point and must know the
shape of the runtime's own settings, so it imports :mod:`helios.runtime.config`
— which imports nothing from HELIOS at all. Re-exporting the *service* here
would drag it, and therefore :mod:`helios.config`, into that import and make the
two mutually dependent. Import the submodule you want directly; there are four
and this docstring names them all.
"""
