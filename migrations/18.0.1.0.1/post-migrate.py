# -*- coding: utf-8 -*-


def migrate(cr, version):
    """Conversations created before the retry still show "Customer 1234": try their profile again."""
    cr.execute("""
        UPDATE omnichat_conversation
           SET profile_pending = TRUE
         WHERE name ~ '^(Customer|Khách hàng) [0-9A-Za-z_-]{1,4}$'
    """)
