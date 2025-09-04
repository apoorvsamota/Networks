#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#include <bits/stdc++.h>
using namespace std;

struct Cfg {
    string ip="0.0.0.0"; int port=9090; string file="words.txt";
} cfg;

static string slurp(const string& path){
    ifstream f(path); string s((istreambuf_iterator<char>(f)), {});
    return s;
}
static string trim(const string& s){ size_t a=s.find_first_not_of(" \r\n\t"), b=s.find_last_not_of(" \r\n\t"); return a==string::npos? "": s.substr(a,b-a+1); }
static string getStr(const string& j, const string& key){
    auto p=j.find("\""+key+"\""); if(p==string::npos) return "";
    p=j.find(':',p); p=j.find('"',p); auto q=j.find('"',p+1); return j.substr(p+1, q-p-1);
}
static int getInt(const string& j, const string& key){
    auto p=j.find("\""+key+"\""); if(p==string::npos) return 0;
    p=j.find(':',p)+1; while(p<j.size() && isspace(j[p])) p++;
    int sgn=1; if(j[p]=='-'){ sgn=-1; p++; }
    int v=0; while(p<j.size() && isdigit(j[p])) v=v*10+(j[p++]-'0'); return sgn*v;
}

int main(){
    ios::sync_with_stdio(false);
    string j = slurp("config.json");
    if(j.size()){ cfg.ip=getStr(j,"server_ip"); cfg.port=getInt(j,"server_port"); string fn=getStr(j,"filename"); if(!fn.empty()) cfg.file=fn; }

    // load words.txt into vector
    vector<string> words;
    string all=slurp(cfg.file);
    string cur; for(char c: all){ if(c==','){ if(!cur.empty()) words.push_back(trim(cur)); cur.clear(); } else cur.push_back(c); }
    if(!trim(cur).empty()) words.push_back(trim(cur));

    // socket listen
    int sfd = socket(AF_INET, SOCK_STREAM, 0);
    int yes=1; setsockopt(sfd, SOL_SOCKET, SO_REUSEADDR, &yes, sizeof(yes));
    sockaddr_in addr{}; addr.sin_family=AF_INET; addr.sin_port=htons(cfg.port);
    // NEW: bind to server_ip if given; else 0.0.0.0
    if(!cfg.ip.empty()){
        if(inet_pton(AF_INET, cfg.ip.c_str(), &addr.sin_addr) != 1){
            perror("inet_pton(server_ip)"); return 1;
        }
    } else {
        addr.sin_addr.s_addr = INADDR_ANY;
    }
    if(bind(sfd,(sockaddr*)&addr,sizeof(addr))<0){ perror("bind"); return 1; }
    if(listen(sfd, 16)<0){ perror("listen"); return 1; }
    cerr<<"Server listening on port "<<cfg.port<<" ("<<cfg.file<<")\n";

    auto respond=[&](int cfd){
        // read one line "p,k\n"
        string line; char buf[512];
        while(true){
            ssize_t n=recv(cfd, buf, sizeof(buf), 0);
            if(n<=0) return;
            line.append(buf, buf+n);
            if(line.find('\n')!=string::npos) break;
        }
        // parse p,k
        int p=0,k=0; {
            auto nl=line.find('\n'); if(nl!=string::npos) line.resize(nl);
            auto comma=line.find(','); if(comma!=string::npos){
                p=stoi(line.substr(0,comma)); k=stoi(line.substr(comma+1));
            }
        }
        string out;
        if(p<0 || k<=0 || p>=(int)words.size()){ out="EOF\n"; }
        else{
            int sent=0;
            for(int i=p; i<(int)words.size() && sent<k; ++i,++sent){
                if(sent) out.push_back(',');
                out += words[i];
            }
            if(sent<k || p+sent>=(int)words.size()) out += (sent? ",EOF\n":"EOF\n");
            else out.push_back('\n');
        }
        send(cfd, out.data(), out.size(), 0);
    };

    // accept loop: 1 request per connection (simple & per spec)
    while(true){
        sockaddr_in cli{}; socklen_t cl=sizeof(cli);
        int cfd=accept(sfd,(sockaddr*)&cli,&cl);
        if(cfd<0){ perror("accept"); continue; }
        respond(cfd);
        close(cfd);
    }
    return 0;
}
