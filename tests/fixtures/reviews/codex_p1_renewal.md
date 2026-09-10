 P1 Badge Incorporate later recurring add-on mutations into renewal terms

When a contract has a prior subscription_change_applied record and an admin subsequently uses a recurring add-on add/update/remove endpoint, that endpoint updates BillingContractAddon but records only a recurring_addon_* history event. _get_frozen_renewal_terms() continues loading the older subscription-change snapshot, so this comparison rejects the now-correct live add-on set and skips the contract's next renewal. Compose later recurring add-on mutations into the frozen terms or refresh the renewal snapshot when applying them.

Useful? React with 👍 / 👎.
