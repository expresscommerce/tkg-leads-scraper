import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(217 33% 17%)",
        background: "hsl(222 47% 4%)",
        foreground: "hsl(213 31% 91%)",
        card: "hsl(222 47% 6%)",
        primary: "hsl(217 91% 60%)",
        muted: "hsl(217 33% 17%)",
        "muted-foreground": "hsl(215 20% 65%)",
      },
    },
  },
  plugins: [],
};

export default config;
