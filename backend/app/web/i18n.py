"""Traduction de l'interface : le texte source est le français, AR fournit l'arabe.

Choix volontaire d'un dictionnaire Python plutôt que des fichiers .po/.mo : aucune étape de
compilation au déploiement, et un test (tests/test_web.py) vérifie qu'aucune chaîne des
gabarits n'est sans traduction.
"""
from flask_babel import get_locale

from .translations_ar import AR


def translate(text: str, **kwargs) -> str:
    try:
        lang = str(get_locale() or "fr")
    except Exception:  # hors contexte de requête
        lang = "fr"
    result = AR.get(text, text) if lang == "ar" else text
    if kwargs:
        try:
            result = result.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    return result


_ = translate
