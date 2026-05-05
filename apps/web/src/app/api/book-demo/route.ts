import { NextRequest, NextResponse } from 'next/server';

export async function POST(req: NextRequest) {
  const body = await req.json();
  const { name, email, company, role, teamSize, message } = body;

  if (!name || !email || !company) {
    return NextResponse.json({ error: 'Name, email, and company are required.' }, { status: 400 });
  }

  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(email)) {
    return NextResponse.json({ error: 'Invalid email address.' }, { status: 400 });
  }

  const textBody = `
New Demo Request — OpsLens AI

Name:      ${name}
Email:     ${email}
Company:   ${company}
Role:      ${role || 'Not provided'}
Team Size: ${teamSize || 'Not provided'}
Message:   ${message || 'Not provided'}

Submitted at: ${new Date().toISOString()}
  `.trim();

  const apiKey = process.env.SENDGRID_API_KEY;
  const toEmail = process.env.DEMO_REQUEST_EMAIL || 'admin@opslensai.com';

  if (apiKey) {
    const sgRes = await fetch('https://api.sendgrid.com/v3/mail/send', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        personalizations: [{ to: [{ email: toEmail }] }],
        from: { email: 'noreply@opslensai.com', name: 'OpsLens AI' },
        reply_to: { email, name },
        subject: `Demo Request from ${name} at ${company}`,
        content: [{ type: 'text/plain', value: textBody }],
      }),
    });

    if (!sgRes.ok) {
      console.error('SendGrid error:', await sgRes.text());
      return NextResponse.json({ error: 'Failed to send email.' }, { status: 500 });
    }
  } else {
    // No SendGrid key configured — log for visibility in dev/staging
    console.log('[DEMO REQUEST]', textBody);
  }

  return NextResponse.json({ ok: true });
}
