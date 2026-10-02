import withMT from "@material-tailwind/react/utils/withMT";
import typography from '@tailwindcss/typography';

export default withMT({
  content: ["./index.html", "./src/**/*.{vue,js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        primary: {"50":"#f0f9ff","100":"#e0f2fe","200":"#bae6fd","300":"#7dd3fc","400":"#38bdf8","500":"#0ea5e9","600":"#0284c7","700":"#0369a1","800":"#075985","900":"#0c4a6e","950":"#082f49"}
      },
      maxWidth: {
        'screen-xl': '1800px',  // Example: Custom max width for extra large screens
      },      
      // Set Arimo as the default sans font for the site
      fontFamily: {
        sans: ["Arimo", "ui-sans-serif", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"],
      },
    },
  },
  plugins: [
    typography,
  ],
});
