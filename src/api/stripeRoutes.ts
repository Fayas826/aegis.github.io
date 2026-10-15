import express from 'express';
import Stripe from 'stripe';

const router = express.Router();
// Use a mocked or test key initially. For absolute reality, replace this via the .env file.
const stripe = new Stripe(process.env.STRIPE_SECRET_KEY || 'sk_test_mockKeyForNow', {
  // Let the installed SDK select its current compatible API version rather
  // than pinning a version that its TypeScript declarations reject.
});

router.post('/create-checkout-session', async (req, res) => {
  try {
    const session = await stripe.checkout.sessions.create({
      payment_method_types: ['card'],
      line_items: [
        {
          price_data: {
            currency: 'usd',
            product_data: {
              name: 'JARVIS Enterprise API Usage',
              description: 'Pay-as-you-go invoice for 24,560 requests.',
            },
            unit_amount: 425000, // ,250.00 in cents
          },
          quantity: 1,
        },
      ],
      mode: 'payment',
      success_url: 'http://localhost:5173/success',
      cancel_url: 'http://localhost:5173/cancel',
    });
    res.json({ id: session.id, url: session.url });
  } catch (error) {
    res.status(500).json({ error: (error as Error).message });
  }
});

export default router;
