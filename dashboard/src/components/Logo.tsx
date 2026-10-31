/** AfoninWebStudio brand: the studio's own AW mark and wordmark images (public/brand). */
export function LogoMark({size = 36}: {size?: number}) {
  return <img className="logo-mark" src="/brand/aw-mark.png" width={size} height={size} alt="" aria-hidden="true"/>;
}

export function Brand({product = 'Support Desk'}: {product?: string}) {
  return <div className="brand">
    <LogoMark/>
    <span className="brand-text"><strong>{product}</strong><small>AfoninWebStudio</small></span>
  </div>;
}

/** Full horizontal logo for large surfaces (sign-in page). */
export function Wordmark() {
  return <img className="wordmark" src="/brand/afonin-web-studio.jpg" alt="Afonin Web Studio — we build digital solutions that grow"/>;
}
