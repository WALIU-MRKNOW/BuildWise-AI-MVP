"""Explicitly allowlisted test checkout commands, independent of paid access."""
from telegram.ext import CommandHandler, filters

from bot.services.payment_ledger import SandboxPayments
from bot.services.paystack_service import PaystackTestService, PaystackError
from bot.services.supabase_service import SupabaseError


class PaymentTestHandlers:
    def __init__(self, settings, database, student_handlers):
        self.settings = settings
        self.database = database
        self.students = student_handlers
        self.allowed = settings.paystack_test_allowed_telegram_ids
        self.payments = SandboxPayments(database, PaystackTestService(settings))

    def register(self, application):
        if self.settings.paystack_test_enabled:
            self.payments.provider._test_key()
            for name in ['premiumtest', 'premiumtestcheck', 'premiumteststatus']:
                application.add_handler(CommandHandler(name, self.command, filters=filters.ChatType.PRIVATE))

    async def command(self, update, context):
        if not self.settings.paystack_test_enabled or update.effective_user.id not in self.allowed:
            await update.effective_message.reply_text('Payment sandbox is restricted to approved testers.')
            return
        profile = await self.students._profile(update)
        command = update.effective_message.text.split()[0].split('@')[0]
        try:
            if command == '/premiumteststatus':
                active = await self.database._rpc('test_premium_active', {'p_user_id': profile.id})
                text = f"Sandbox monthly entitlement: {'active' if active else 'inactive or expired'}. Test only; your real access and limits are unchanged."
            elif command == '/premiumtestcheck':
                if not context.args:
                    rows = await self.database._request('GET', '/rest/v1/paystack_test_intents', params={
                        'user_id': f'eq.{profile.id}', 'select': 'reference', 'order': 'created_at.desc', 'limit': '1'})
                    if not rows:
                        raise PaystackError('No test checkout exists for this student')
                    reference = rows[0]['reference']
                else:
                    reference = context.args[0]
                state = await self.payments.reconcile(reference, profile.id)
                text = f'Sandbox payment state: {state}. Test only; real Premium remains disabled.'
            else:
                if len(context.args) != 1:
                    await update.effective_message.reply_text('Sandbox only: /premiumtest your-test-email\n₦3,000 buys one calendar month in the test ledger. No real payment or access upgrade. Use Paystack test payment details only.')
                    return
                checkout = await self.payments.checkout(profile.id, str(update.update_id), context.args[0])
                text = f'SANDBOX ONLY — ₦3,000 / calendar month\n{checkout.authorization_url}\nReference: {checkout.reference}\nUse /premiumtestcheck after testing payment. No real access is granted.'
        except (PaystackError, SupabaseError, ValueError):
            # A checkout timeout must be reconciled, never blindly re-initialized.
            text = ('The sandbox operation could not complete. Use /premiumtestcheck to reconcile '
                    'your latest saved reference. Your real access is unchanged.')
        await update.effective_message.reply_text(text, disable_web_page_preview=True)
