import { NextRequest, NextResponse } from 'next/server';

export async function POST(req: NextRequest) {
  const body = await req.json();
  const { name, email, company, role, teamSize, preferredDate, message } = body;

  if (!name || !email || !company) {
    return NextResponse.json({ error: 'Name, email, and company are required.' }, { status: 400 });
  }

  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(email)) {
    return NextResponse.json({ error: 'Invalid email address.' }, { status: 400 });
  }

  const textBody = `
New Demo Request — OpsLens AI

Name:           ${name}
Email:          ${email}
Company:        ${company}
Role:           ${role || 'Not provided'}
Team Size:      ${teamSize || 'Not provided'}
Preferred Date: ${preferredDate || 'Not provided'}
Message:        ${message || 'Not provided'}

Submitted at: ${new Date().toISOString()}
  `.trim();

  const apiKey = process.env.RESEND_API_KEY;
  const toEmail = process.env.DEMO_REQUEST_EMAIL || 'admin@opslensai.com';

  if (apiKey) {
    const res = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: 'OpsLens AI <noreply@opslensai.com>',
        to: [toEmail],
        reply_to: email,
        subject: `Demo Request from ${name} at ${company}`,
        text: textBody,
      }),
    });

    if (!res.ok) {
      console.error('Resend error:', await res.text());
      return NextResponse.json({ error: 'Failed to send email.' }, { status: 500 });
    }
  } else {
    // No Resend key configured — log for visibility in dev/staging
    console.log('[DEMO REQUEST]', textBody);
  }

  return NextResponse.json({ ok: true });
}
