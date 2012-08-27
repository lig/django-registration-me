from mongoengine.signals import Namespace

__all__ = ['activate']


_signals = Namespace()

activate = _signals.signal('activate')
