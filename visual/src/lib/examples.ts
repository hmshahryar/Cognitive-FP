/** Commands the backend already understands, one per onboarding goal. */
export const EXAMPLES: Record<"email" | "calendar" | "crm" | "docs" | "social" | "design", string> = {
  email: "Email sara@northwind.example: Thanks for today, I'll send the proposal on Friday.",
  calendar: "Schedule a 30 minute call with sara@northwind.example tomorrow at 3pm.",
  crm: "Add Sara Malik, sara@northwind.example, Northwind Traders to HubSpot.",
  docs: "Create a Notion page called Launch plan.",
  social: "Post on Facebook: We're hiring a part-time designer. Send us a message!",
  design: 'Make a banner for Instagram: headline "Launching Soon", caption: Big news this week',
};
