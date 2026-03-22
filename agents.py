import os
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate

llm = ChatGroq(
    temperature=0, 
    model_name="llama-3.3-70b-versatile", 
    api_key=os.environ.get("GROQ_API_KEY")
)

# The new structure we want: just a clean search term
class SearchTerm(BaseModel):
    clean_name: str = Field(description="The essential distillery, age, and vintage for searching a database.")

structured_llm = llm.with_structured_output(SearchTerm)

# The new prompt
prompt = PromptTemplate.from_template(
    """You are an expert fine spirits data cleaner. 
    Take this messy retail whisky name and extract ONLY the essential terms (Distillery, Age, Cask Type, Vintage) so we can search it on Whiskybase.
    Remove filler words like 'Release', 'The', or 'Old'.
    
    Messy Name: {scraped_name}
    """
)

pricing_agent = prompt | structured_llm

def get_clean_search_term(scraped_name):
    try:
        result = pricing_agent.invoke({"scraped_name": scraped_name})
        return result.clean_name
    except Exception as e:
        print(f"Agent error cleaning {scraped_name}: {e}")
        return scraped_name
