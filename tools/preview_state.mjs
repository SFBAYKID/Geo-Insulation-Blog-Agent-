// This function is serialized into the browser by Playwright.evaluate.
export function articleState(product) {
  const article = document.querySelector('article');
  if (!article) throw new Error('Main article is missing');
  return {
    overflow: document.documentElement.scrollWidth > innerWidth,
    headings: document.querySelectorAll('h1').length,
    productLinks: Array.from(article.querySelectorAll('a')).filter(a => a.href === product).length,
    images: Array.from(article.querySelectorAll('img')).map(i => ({alt: i.alt, loaded: i.complete && i.naturalWidth > 0})),
    noindex: document.querySelector('meta[name="robots"]')?.content.includes('noindex'),
  };
}
