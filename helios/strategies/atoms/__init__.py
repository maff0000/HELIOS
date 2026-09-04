"""The proof atoms.

Every module in this package is an independent atomic strategy. None of them
imports, references, calls or knows about any other, and this package
deliberately re-exports nothing: importing one atom must not drag its siblings
into the process, because that is the property ``tests/test_isolation.py``
verifies by inspecting a freshly imported interpreter.

The catalogue that names them all for registration lives one level up, in
``helios.strategies.catalogue``.

These six exist to prove the framework — independent evaluation, direction
semantics, the state model, freshness handling and containment. They are not
production alpha and the PID says so explicitly.
"""
