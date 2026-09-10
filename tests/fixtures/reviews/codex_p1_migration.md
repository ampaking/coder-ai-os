 P1 Badge Replace handwritten SQL in the new migrations

This downgrade, along with the other new billing migrations, embeds PostgreSQL statements directly through op.execute() and sa.text(), despite the domain-model migration contract explicitly prohibiting handwritten raw SQL because the shared production recovery path cannot safely support it. Implement the lock and data-presence guard through an approved checked-in migration abstraction instead.

AGENTS.md reference: libs/domain-model/AGENTS.md:L15-L17

Useful? React with 👍 / 👎.
