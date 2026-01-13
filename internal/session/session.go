package session

var Token string

func Set(token string) {
	Token = token
}

func Get() string {
	return Token
}
