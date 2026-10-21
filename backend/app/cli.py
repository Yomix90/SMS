"""Commandes d'administration : `flask <commande>` (ou `python manage.py <commande>`)."""
import getpass
from datetime import date, time, timedelta
from decimal import Decimal

import click
from flask import current_app

from .extensions import db


def register_cli(app):
    @app.cli.command("create-admin")
    @click.option("--email", prompt=True)
    @click.option("--name", default=None)
    @click.option("--password", default=None, help="Demandé interactivement si absent")
    def create_admin(email, name, password):
        """Créer un administrateur."""
        from .services.accounts import AccountError, create_user
        password = password or getpass.getpass("Mot de passe : ")
        try:
            user = create_user(email, password, role="admin", full_name=name)
        except AccountError as exc:
            raise click.ClickException(f"Impossible de créer l'utilisateur : {exc}")
        db.session.commit()
        click.echo(f"Administrateur créé : {user.email}")

    @app.cli.command("create-api-key")
    @click.option("--name", required=True)
    @click.option("--scopes", default="*", help="Permissions séparées par des virgules (* = toutes)")
    @click.option("--rate", default=60, help="Requêtes par minute")
    @click.option("--quota", default=None, type=int, help="SMS par jour")
    @click.option("--outside-window", is_flag=True, help="Autoriser l'envoi hors fenêtre horaire")
    def create_api_key_cmd(name, scopes, rate, quota, outside_window):
        """Créer une clé API (affichée une seule fois)."""
        from .services.accounts import create_api_key
        key, token = create_api_key(name, [s.strip() for s in scopes.split(",")], rate_limit_per_min=rate,
                                    daily_quota=quota, allow_outside_window=outside_window)
        db.session.commit()
        click.echo(f"Clé « {key.name} » créée. Conservez-la, elle ne sera plus affichée :\n{token}")

    @app.cli.command("pairing-code")
    @click.option("--name", default=None, help="Nom de l'appareil")
    @click.option("--svg", "svg_path", default=None, help="Enregistrer aussi le QR dans un fichier SVG")
    def pairing_code(name, svg_path):
        """Générer un QR d'appairage dans le terminal (alternative au tableau de bord)."""
        import qrcode
        from .services.devices import create_pairing_code, pairing_payload, pairing_qr_svg
        _pc, code = create_pairing_code(device_name=name)
        db.session.commit()
        payload = pairing_payload(code)
        click.echo(f"Contenu : {payload}")
        click.echo(f"Valable {current_app.config['PAIRING_CODE_TTL_MIN']} minutes.")
        if svg_path:
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(pairing_qr_svg(code))
            click.echo(f"QR enregistré : {svg_path}")
        qr = qrcode.QRCode(border=1)
        qr.add_data(payload)
        try:
            qr.print_ascii(invert=True)
        except UnicodeEncodeError:  # console Windows non UTF-8
            click.echo("(Terminal non UTF-8 : utilisez --svg fichier.svg ou PYTHONIOENCODING=utf-8)")

    @app.cli.command("rotate-keys")
    def rotate_keys():
        """Re-chiffrer les secrets avec la clé primaire de APP_ENCRYPTION_KEYS."""
        from .models import Webhook
        from .services.security import rotate
        hooks = db.session.query(Webhook).all()
        for hook in hooks:
            hook.secret_encrypted = rotate(hook.secret_encrypted)
        db.session.commit()
        click.echo(f"{len(hooks)} secret(s) re-chiffré(s). Vous pouvez retirer l'ancienne clé.")

    @app.cli.command("run-job")
    @click.argument("name")
    def run_job(name):
        """Exécuter un job planifié immédiatement (dispatcher, schedules, automations…)."""
        from .tasks import run_job_now
        try:
            click.echo(run_job_now(name))
        except KeyError:
            raise click.ClickException(f"Job inconnu : {name}")

    @app.cli.command("seed-demo")
    def seed_demo():
        """Données de démonstration : groupes, contacts, échéances, modèles, règles."""
        from .models import (Automation, Contact, Group, KeywordRule, Receivable, Template)
        from .services.templating import extract_variables
        if db.session.query(Contact).count():
            raise click.ClickException("La base contient déjà des contacts")
        clients = Group(name="Clients", color="#0d6efd")
        vip = Group(name="VIP", color="#d63384")
        db.session.add_all([clients, vip])
        people = [("Salma", "Alaoui", "+212612345678", "fr"), ("Youssef", "Bennani", "+212661234567", "fr"),
                  ("Fatima", "El Idrissi", "+212700112233", "ar"), ("Omar", "Tazi", "+212655443322", "fr")]
        for i, (fn, ln, phone, loc) in enumerate(people):
            c = Contact(first_name=fn, last_name=ln, phone=phone, locale=loc, custom_fields={"ville": "Casablanca"},
                        consent_status="opt_in", consent_source="demo", birthday=date(1990, 1 + i, 10 + i))
            c.groups.append(clients)
            if i == 0:
                c.groups.append(vip)
            db.session.add(c)
            db.session.flush()
            db.session.add(Receivable(contact_id=c.id, reference=f"FAC-2026-{100 + i}",
                                      amount=Decimal("850.00") + i * 200, due_date=date.today() + timedelta(days=3 * i),
                                      extra={}))
        templates = [
            ("Rappel échéance", "rappel", "fr",
             "Bonjour {prenom}, votre échéance {reference} de {montant} MAD arrive le {date_echeance}. Merci."),
            ("Rappel échéance", "rappel", "ar",
             "مرحبا {prenom}، نذكركم بأن قسط {reference} بمبلغ {montant} درهم يحل أجله في {date_echeance}. شكرا."),
            ("Relance impayé", "relance", "fr",
             "Bonjour {prenom}, sauf erreur, la facture {reference} ({montant} MAD) échue le {date_echeance} "
             "reste impayée. Merci de régulariser."),
            ("Anniversaire", "fidelite", "fr", "Joyeux anniversaire {prenom} ! Toute l'équipe vous souhaite une belle journée."),
            ("Bienvenue", "bienvenue", "fr", "Bienvenue {prenom} ! Merci de votre confiance."),
        ]
        tpl = {}
        for name, cat, lang, body in templates:
            t = Template(name=name, category=cat, language=lang, body=body, variables=extract_variables(body))
            db.session.add(t)
            tpl[(name, lang)] = t
        db.session.flush()
        db.session.add_all([
            Automation(name="Rappel J-3 et J-1", trigger="due_reminder", config={"days_before": [3, 1]},
                       template_fr_id=tpl[("Rappel échéance", "fr")].id,
                       template_ar_id=tpl[("Rappel échéance", "ar")].id, run_time=time(10, 0)),
            Automation(name="Relance impayés", trigger="unpaid_followup", config={"days_after": [1, 7, 15], "max": 3},
                       template_fr_id=tpl[("Relance impayé", "fr")].id, run_time=time(11, 0), is_active=False),
            Automation(name="Anniversaires", trigger="birthday", template_fr_id=tpl[("Anniversaire", "fr")].id,
                       run_time=time(9, 30)),
            KeywordRule(keywords=["INFO", "معلومات"], match_type="exact", action="reply",
                        reply_body="Nos horaires : du lundi au samedi, 9h-18h. Tél : 05 22 00 00 00."),
        ])
        db.session.commit()
        click.echo("Données de démonstration créées.")
