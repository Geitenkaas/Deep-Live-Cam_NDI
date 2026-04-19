import argparse
import requests
from io import BytesIO
from PIL import Image
from ddgs import ddgs


def search_images(query, max_results=5):
  """Searches for images using DuckDuckGo and prints the URLs."""
  print(f"Searching for: '{query}'...\n" + "-"*30)
  
  # Initialize the DuckDuckGo search client
  with ddgs.DDGS() as ddgs_search:
    # Fetch image results
    results = ddgs_search.images(
        query=query,
        region="wt-wt", # Worldwide
        safesearch="moderate",
        max_results=max_results
    )
    
    # Parse and print the results
    for index, result in enumerate(results, start=1):
      title = result.get('title')
      image_url = result.get('image')
      source = result.get('url')
      
      print(f"Result {index}:")
      print(f"Title:  {title}")
      print(f"Image:  {image_url}")
      print(f"Source: {source}\n")
      try:
        print(f"Downloading and displaying image {index}...")
        response = requests.get(image_url, timeout=10)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        img.show()
      except Exception as e:
        print(f"Could not display image: {e}\n")


# Run the search
if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="Test DuckDuckGo image search.")
  parser.add_argument("-q", "--query", type=str, default="Ayrton Senna", help="The search query.")
  parser.add_argument("-m", "--max_results", type=int, default=1, help="Maximum number of results to fetch.")
  args = parser.parse_args()
  
  search_images(args.query, max_results=args.max_results)
