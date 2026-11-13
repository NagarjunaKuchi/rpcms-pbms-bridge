{
    "name": "RPCMS: PBMS to G2P Bridge mapping",
    "summary": "Send UNHCR ProGres IDs and per-cycle codes to the G2P Bridge (Duplicate Guard convention)",
    "category": "G2P",
    "version": "17.0.0.2.0",
    "author": "RPCMS",
    "license": "LGPL-3",
    "depends": ["g2p_payment_g2p_connect"],
    "data": [
        "data/id_type.xml",
        "views/payment_manager_view.xml",
    ],
    "installable": True,
    "application": False,
}
