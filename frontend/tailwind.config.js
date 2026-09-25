/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#172126',
        canvas: '#f6f7f8',
        brand: '#c5f36a',
        muted: '#718087',
      },
      boxShadow: {
        card: '0 10px 35px rgba(22, 39, 45, 0.045)',
      },
    },
  },
  plugins: [],
};
