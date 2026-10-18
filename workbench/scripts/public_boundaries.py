"""Explicit unavailable boundaries for private live adapters."""
class RetryConflict(ValueError): pass
class ReviewConflict(ValueError): pass
class MarketReadError(ValueError): pass
class HistoryProvider:
    def __init__(self,*args,**kwargs):
        raise MarketReadError('live_history_adapter_not_in_public_package')
