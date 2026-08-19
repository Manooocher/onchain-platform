"""Exception hierarchy — the shared contract every layer needs.

DOC-013 § Exception Hierarchy: any exception that would cross a Capability
boundary is translated to a PlatformError subclass first. Infrastructure
exceptions (SQLAlchemyError, httpx errors, redis errors) leaking past a
boundary un-translated are a bug in the module that let them through.

Lives in domain/ alongside schemas/, entities/, and ids.py because it is a
shared contract, not a Capability-owned concern (DOC-013).
"""


class PlatformError(Exception):
    """Base for every exception raised across a Capability boundary.

    A bare Exception, SQLAlchemyError, or httpx error crossing a boundary
    un-translated is a bug in the module that let it through (DOC-013 §
    Exception Hierarchy).
    """


class DomainValidationError(PlatformError):
    """A business rule was violated after Pydantic's own field-level
    validation already passed — e.g. a raw log that cannot be decoded into
    any known event shape (DOC-012 § B.1).

    Deliberately NOT named ValidationError: pydantic.ValidationError already
    owns that name, and a same-named sibling class is a guaranteed source of
    wrong except clauses and shadowed imports (DOC-013).
    """


class PersistenceError(PlatformError):
    """Raised at the persistence/ boundary. Includes the row-level
    immutability guard in facts.py — DOC-013 § Immutability & State
    Modeling: a violation raises, never silently no-ops.
    """


class AcquisitionError(PlatformError):
    """Raised at the acquisition/ boundary — RPC timeout, rate limit,
    malformed provider payload. Never a raw httpx or provider-SDK exception
    past this point (DOC-013 § Exception Hierarchy).
    """


class SchemaVersionError(PlatformError):
    """The Schema Version Dispatcher (DOC-010 § Data Processing) received a
    schema_version it has no parser for.
    """


class TransportError(PlatformError):
    """Raised at the transport/ boundary — Redis connection loss, Stream
    write failure, or Consumer Group error. Never a raw redis-py exception
    past this point (DOC-013 § Exception Hierarchy).
    """
