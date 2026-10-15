import puppeteer from 'puppeteer';
import Post, { PostSource } from '../models/Post';

export class InternetScout {
  /**
   * UNIVERSAL WEB SCOUT: Fetches content from ANY URL using Puppeteer.
   */
  static async fetchExternalPosts(url: string, topicName: string = 'General'): Promise<void> {
    try {
      console.log(`InternetScout: Launching Universal Uplink to ${url}...`);
      
      const browser = await puppeteer.launch({ headless: true });
      const page = await browser.newPage();
      
      // Simulate real user agent
      await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36');
      
      await page.goto(url, { waitUntil: 'networkidle2' });
      
      // Universally extract the title and main text content of the page
      const pageData = await page.evaluate(() => {
        const title = document.title;
        // Grab paragraphs or body text as a rough universal scrape
        const paragraphs = Array.from(
          document.querySelectorAll<HTMLElement>('p, article, .post, .message'),
        )
          .map((p: HTMLElement) => p.textContent?.trim())
          .filter((t): t is string => Boolean(t && t.length > 50));
        const content = paragraphs.slice(0, 5).join('\n\n'); // Get top 5 paragraphs
        return { title, content };
      });
      
      await browser.close();

      // Save to A.E.G.I.S Message Board
      const newPost = new Post({
        title: pageData.title || `Scraped: ${url}`,
        content: pageData.content || `No readable text found at ${url}`,
        author: 'A.E.G.I.S. Universal Web Agent',
        source: PostSource.OTHER,
        url: url,
        tags: [topicName, 'universal-scrape']
      });
      
      await newPost.save();
      console.log(`InternetScout: Successfully archived data from ${url}`);
      
    } catch (error) {
      console.error(`InternetScout Error while accessing ${url}:`, error);
    }
  }
}
