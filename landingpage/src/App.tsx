import { Nav } from "./components/Nav";
import { Hero } from "./components/Hero";
import { TrustedBy } from "./components/TrustedBy";
import { FeatureGrid } from "./components/FeatureGrid";
import { Frameworks } from "./components/Frameworks";
import { HowItWorks } from "./components/HowItWorks";
import { Pricing } from "./components/Pricing";
import { CTASection } from "./components/CTASection";
import { Footer } from "./components/Footer";

export default function App() {
  return (
    <div className="min-h-screen bg-paper">
      <Nav />
      <main>
        <Hero />
        <TrustedBy />
        <FeatureGrid />
        <Frameworks />
        <HowItWorks />
        <Pricing />
        <CTASection />
      </main>
      <Footer />
    </div>
  );
}
