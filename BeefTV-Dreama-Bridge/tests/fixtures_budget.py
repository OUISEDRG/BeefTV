"""Synthetic budgets for existing Mock regression tests, never production defaults."""
from bridge import Bridge as ProductionBridge

def funded_policy():
    return {'schemaVersion':1,'enabled':True,'dailyLimitPoints':'1000000',
            'perOperationLimits':{k:'1000000' for k in ('video','image','extension','enhancement','audio')},
            'timezone':'UTC','dayStart':'00:00'}

class FundedBridge(ProductionBridge):
    def __init__(self,*args,**kwargs):
        kwargs.setdefault('budget_policy',funded_policy())
        super().__init__(*args,**kwargs)

def intent_fields(amount):
    return {'submissionIntentId':'a'*32,'reservedPoints':str(amount),'quotedPoints':str(amount),'pointsEstimate':amount}
