🔍 AI Review Result

✅ Good

libs/domain-model changes are additive-only: every new/altered column is nullable or has a safe
server_default, and every new migration's downgrade() actively queries for existing evidence and
raises rather than silently dropping data. The migration chain is a single linear sequence.
Money handling is disciplined: Decimal throughout, a single quantize_invoice_money helper.

🚨 CRITICAL

Missing is_service_provider authorization check on the billing-discount catalog and billing
audit-history endpoints. In packages/admin-api/app/application/use_cases/admin/billing_discounts.py,
GetBillingDiscountsUseCase.execute() takes no operator_context at all, and
SaveBillingDiscountUseCase.execute() takes operator_context but never checks
operator_context.is_service_provider before creating/updating a discount. Likewise
GetBillingManagementHistoryUseCase.execute() takes no operator_context.
Impact: any operator with OperatorRole.SYSTEM_ADMIN but is_service_provider=False can list every
organization's billing-management history including negotiated prices and discount snapshots.
Fix: add the same role/is_service_provider permission_denied check used everywhere else in this PR.

⚠️ WARNING

Review evidence for ~156 of 282 changed files is unavailable. review.diff contains no diff hunks at
all for any file under packages/admin-web/**, packages/user-web/**, packages/user-api/**. Per the
review's own rules, this is reported as a WARNING rather than assumed passing.
Dead code: _missing_periods() in get_contract_subscriptions.py is still defined but is no longer
called anywhere. Remove the orphaned function.

💡 SUGGESTION

manage_invoice_correction.py: the "later commercial reduction must reduce the total" guard has no
analogous sanity check for other correction_type values; worth double-checking the schema's full
enum of correction_type values got equivalent guards.

Checklist

 Security (auth / authz / input validation) — gap found, see CRITICAL
 Type safety (TypeScript strict / mypy strict; no any/Any) — reviewed portion is clean
 Architecture (Clean Architecture / Result[T,E] / TypedDict) — follows the layered pattern
 Config layer (no raw os.getenv in main/handlers/routes) — none found in reviewed diff
 i18n (no hardcoded strings) — not verifiable; admin-web locale files were not available
 Backward compatibility (callers checked) — checked where evidence existed
 DB contract (model<->migration match / reversible / expand-contract) — verified
 Test coverage (named scenarios) — not fully verifiable
 UI edge cases — not verifiable
 Code quality — the reviewed code is unusually careful; one dead-code leftover noted

AI PR review powered by Claude
