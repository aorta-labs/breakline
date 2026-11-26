import { shopifyApp } from "@shopify/shopify-app-remix/server";
import OpenAI from "openai";

const openai = new OpenAI();

export const shopify = shopifyApp({
  apiKey: process.env.SHOPIFY_API_KEY!,
  accessMode: "offline",
  future: { expiringOfflineAccessTokens: false },
});

export async function productBlurb(title: string): Promise<string> {
  const r = await openai.chat.completions.create({
    model: "gpt-4o-mini-2024-07-18",
    messages: [{ role: "user", content: `Write a 20-word blurb for ${title}` }],
  });
  return r.choices[0].message.content ?? "";
}
